# -*- coding: utf-8 -*-
"""PDF/DOCX/TXT 文件提取与质量诊断，保留可验证位置。"""
import html
import io
import os
import re
import zipfile


ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip", "application/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
}


def validate_document(filename, data, content_type=None):
    """Verify extension, declared MIME and file signature before parsing."""
    if not filename or not str(filename).strip():
        raise ValueError("文件名为空")
    if not data:
        raise ValueError("文件内容为空")
    ext = os.path.splitext(str(filename))[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的文件类型：%s（支持 docx / pdf / txt / md）" % (ext or "无扩展名"))
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime and mime not in MIME_BY_EXTENSION[ext]:
        raise ValueError("文件 MIME 类型与扩展名不匹配：%s / %s" % (mime, ext))
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("文件扩展名为 PDF，但内容不是有效 PDF")
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" not in zf.namelist():
                    raise ValueError("文件不是有效 DOCX（缺少 Word 正文）")
        except zipfile.BadZipFile:
            raise ValueError("文件扩展名为 DOCX，但内容不是有效 DOCX")
    return ext


def _mask_sensitive(text):
    patterns = [
        (r"(?<!\d)(\d{6})\d{8}(\d{4})(?!\d)", r"\1********\2"),
        (r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)", r"\1********\2"),
        (r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)", r"\1****\2"),
    ]
    out = text
    for pat, repl in patterns:
        out = re.sub(pat, repl, out)
    return out


def _pdf(data):
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError("服务端未安装 PDF 解析器 PyMuPDF，请运行 pip install -r requirements.txt 后重启服务") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF 文件损坏或无法打开：%s" % exc)
    if doc.needs_pass:
        raise PermissionError("PDF 已加密，请解除密码保护后重新上传")
    pages, scan_pages, blank_pages, signature_pages, low_quality, table_count = [], [], [], [], [], 0
    for i, page in enumerate(doc):
        page_no = i + 1
        text = page.get_text("text").strip()
        try:
            table_count += len(page.find_tables().tables)
        except Exception:
            pass
        image_count = len(page.get_images(full=True))
        if not text and not image_count:
            blank_pages.append(page_no)
        if len(text) < 25 and image_count:
            scan_pages.append(page_no)
            low_quality.append(page_no)
        if re.search(r"签字|签章|盖章|法定代表人", text[-300:]) and len(text) < 500:
            signature_pages.append(page_no)
        pages.append("【第%d页】\n%s" % (page_no, text))
    warnings = []
    if scan_pages:
        warnings.append("检测到扫描版页面，当前暂不支持高可信解析，请上传文本版 PDF")
    diagnostics = {"format": "pdf", "status": "needs_text_version" if scan_pages else "success", "pages": len(doc),
                   "scan_pages": scan_pages, "ocr_pages": [], "ocr_page_count": 0,
                   "blank_pages": blank_pages, "signature_pages": signature_pages,
                   "low_quality_pages": low_quality, "table_count": table_count,
                   "warnings": warnings}
    return "\n".join(pages).strip(), diagnostics


def _docx(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as exc:
        raise ValueError("DOCX 解析失败：%s" % exc)
    body = re.search(r"<w:body[\s\S]*?</w:body>", xml)
    xml = body.group(0) if body else xml
    blocks, paragraph_count, table_count = [], 0, 0
    token_pat = re.compile(r"<w:tbl[\s\S]*?</w:tbl>|<w:p[\s\S]*?</w:p>")
    for block in token_pat.findall(xml):
        if block.startswith("<w:tbl"):
            table_count += 1
            rows = re.findall(r"<w:tr[\s\S]*?</w:tr>", block)
            for ri, row in enumerate(rows, 1):
                cells = []
                for cell in re.findall(r"<w:tc[\s\S]*?</w:tc>", row):
                    txt = "".join(html.unescape(x) for x in re.findall(r"<w:t[^>]*>([\s\S]*?)</w:t>", cell))
                    cells.append(txt.strip())
                if any(cells):
                    blocks.append(" | ".join(cells))
        else:
            txt = "".join(html.unescape(x) for x in re.findall(r"<w:t[^>]*>([\s\S]*?)</w:t>", block)).strip()
            if txt:
                paragraph_count += 1
                blocks.append(txt)
    attachments = [n for n in zf.namelist() if n.startswith("word/embeddings/")]
    diagnostics = {"format": "docx", "status": "success", "pages": None, "page_note": "DOCX 无可靠真实页码，使用段落/表格位置",
                   "paragraph_count": paragraph_count, "table_count": table_count, "attachment_count": len(attachments),
                   "ocr_page_count": 0, "blank_pages": [], "low_quality_pages": [], "warnings": []}
    return "\n".join(blocks).strip(), diagnostics


def extract_document(filename, data, content_type=None):
    ext = validate_document(filename, data, content_type)
    if ext == ".pdf": text, diag = _pdf(data)
    elif ext == ".docx": text, diag = _docx(data)
    elif ext in (".txt", ".md"):
        text = None
        for enc in ("utf-8", "gbk", "gb18030"):
            try: text = data.decode(enc); break
            except UnicodeDecodeError: pass
        text = text or data.decode("utf-8", errors="ignore")
        diag = {"format": ext[1:], "status": "success", "pages": None, "page_note": "纯文本使用字符位置", "ocr_page_count": 0,
                "paragraph_count": len([x for x in text.splitlines() if x.strip()]), "table_count": 0, "warnings": []}
    else:
        raise ValueError("不支持的文件类型：%s（支持 docx / pdf / txt）" % ext)
    diag["sensitive_matches"] = len(re.findall(r"\b(?:1[3-9]\d{9}|\d{16,19}|\d{17}[\dXx])\b", text))
    return {"text": text, "masked_text": _mask_sensitive(text), "diagnostics": diag}


mask_sensitive = _mask_sensitive
