# -*- coding: utf-8 -*-
"""AI接口适配器：OpenAI 兼容协议（DeepSeek/智谱/通义/Kimi/OpenAI）
- 流式 SSE 输出
- 无 Key 时内置智能引擎兜底（基于分析结果的结构化解释，数字始终来自确定性引擎）
"""
import json
import os
import time
import threading
import urllib.request
import urllib.error
import socket

CONFIG = {
    "provider": "openai",
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "proxy": "",
    "timeout": 25,
    "retries": 2,
}

# 常见本机代理端口（Clash/V2Ray/SS 等），连接失败时自动探测
COMMON_PROXIES = [
    "http://127.0.0.1:7897", "http://127.0.0.1:7890", "http://127.0.0.1:1087",
    "http://127.0.0.1:10809", "http://127.0.0.1:8118", "http://127.0.0.1:8889",
    "http://127.0.0.1:33210", "http://127.0.0.1:6152",
]

PROVIDER_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "label": "DeepSeek"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "label": "智谱GLM"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "label": "通义千问"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "label": "Kimi"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "label": "OpenAI"},
}

SYSTEM_PROMPT = """你是 ContractGuard 的内嵌AI风控专家，专注于金融合同全生命周期风险管理。
你的职责：
1. 解释合同分析结果（真实融资成本、情景比较、契约监控、版本风险）
2. 帮助用户理解条款的金融含义与风险影响
3. 给出专业、可执行的建议
原则：
- 所有数字（成本、利率、阈值、金额）以用户提供的分析结果为准，绝不自行编造或修改
- 回答简明、结构化，优先使用要点列表
- 涉及重大决策时提示「建议咨询专业律师/财务顾问」
- 用中文回答"""


def _migrate_legacy_key(path, key):
    """One-time migration from legacy config.json storage to backend-only .env."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(path)), ".env")
    if not key or os.path.exists(env_path):
        return
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("CONTRACTGUARD_API_KEY=%s\n" % key)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def _load_env_file(path):
    env_path = os.path.join(os.path.dirname(os.path.abspath(path)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name, value = name.strip(), value.strip().strip("\"'")
                if name and name not in os.environ:
                    os.environ[name] = value
    except FileNotFoundError:
        pass


def load_config(path="config.json"):
    global CONFIG
    _load_env_file(path)
    legacy_key = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
            legacy_key = (saved.get("api_key") or "").strip()
            CONFIG.update({k: v for k, v in saved.items() if k in CONFIG and k != "api_key"})
    except FileNotFoundError:
        pass
    except Exception:
        pass
    env_key = (os.getenv("CONTRACTGUARD_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if env_key:
        CONFIG["api_key"] = env_key
    elif legacy_key:
        CONFIG["api_key"] = legacy_key
        _migrate_legacy_key(path, legacy_key)
        save_config(path)
    for env_name, config_name in (
        ("CONTRACTGUARD_PROVIDER", "provider"),
        ("CONTRACTGUARD_MODEL", "model"),
        ("CONTRACTGUARD_BASE_URL", "base_url"),
        ("CONTRACTGUARD_PROXY", "proxy"),
    ):
        value = (os.getenv(env_name) or "").strip()
        if value:
            CONFIG[config_name] = value
    return CONFIG


def save_config(path="config.json"):
    try:
        with open(path, "w", encoding="utf-8") as f:
            public_config = {k: v for k, v in CONFIG.items() if k != "api_key"}
            json.dump(public_config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_provider(provider):
    if provider in PROVIDER_PRESETS:
        CONFIG["provider"] = provider
        CONFIG["base_url"] = PROVIDER_PRESETS[provider]["base_url"]
        CONFIG["model"] = PROVIDER_PRESETS[provider]["model"]
        # 国内厂商 API 可直连，切换时清除代理设置，避免代理反而干扰
        if provider != "openai":
            CONFIG["proxy"] = ""


def is_configured():
    return bool(CONFIG.get("api_key"))


def status():
    label = PROVIDER_PRESETS.get(CONFIG.get("provider"), {}).get("label", CONFIG.get("provider"))
    key = CONFIG.get("api_key", "")
    return {
        "configured": bool(key), "provider": CONFIG.get("provider"), "label": label,
        "model": CONFIG.get("model"), "base_url": CONFIG.get("base_url"),
        "proxy": CONFIG.get("proxy", ""),
    }


def _http_error_detail(exc):
    """从 HTTPError 响应中提取 API 的具体错误信息"""
    try:
        body = exc.read().decode("utf-8", errors="ignore")
        obj = json.loads(body)
        err = obj.get("error", {}) or {}
        msg = err.get("message") or obj.get("message")
        if msg:
            code = err.get("code") or ""
            return "%s [%s]" % (msg, code) if code else msg
    except Exception:
        pass
    return str(exc)


def test_connection():
    """测试API连通性（连接失败时自动探测本机代理）"""
    if not is_configured():
        return {"ok": False, "message": "未配置 API Key"}
    try:
        msgs = [{"role": "user", "content": "回复OK两个字母即可"}]
        for chunk in _stream_chat(msgs, max_tokens=8):
            if chunk:
                save_config()
                proxy = (CONFIG.get("proxy") or "").strip()
                extra = "（经由本机代理 %s）" % proxy if proxy else ""
                return {"ok": True, "message": "连接成功，模型响应正常" + extra}
        save_config()
        return {"ok": True, "message": "连接成功"}
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        hint = ""
        if "insufficient_quota" in detail or "quota" in detail.lower():
            hint = " —— API Key 配额不足，请到 platform.openai.com 的 Billing 页面充值后再试"
        elif "invalid_api_key" in detail.lower():
            hint = " —— API Key 无效，请检查是否复制完整"
        return {"ok": False, "message": "连接失败：%s%s" % (detail[:200], hint)}
    except Exception as e:
        save_config()
        return {"ok": False, "message": "连接失败：%s" % str(e)[:120]}


def _probe_proxy(save=True):
    """依次尝试常见本机代理端口，返回第一个能连通当前 API 的代理 URL"""
    base = (CONFIG.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    headers = {}
    if CONFIG.get("api_key"):
        headers["Authorization"] = "Bearer %s" % CONFIG["api_key"]
    for p in COMMON_PROXIES:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": p, "https": p}))
            req = urllib.request.Request(base + "/models", headers=headers)
            with opener.open(req, timeout=6) as r:
                if save:
                    CONFIG["proxy"] = p
                return p
        except urllib.error.HTTPError:
            # 服务器有响应（鉴权/限流等），说明代理隧道可用
            if save:
                CONFIG["proxy"] = p
            return p
        except Exception:
            continue
    return None


def _build_opener():
    p = (CONFIG.get("proxy") or "").strip()
    if p:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener()


def _is_conn_error(exc):
    """连接类错误（代理探测的触发条件）：服务器无任何响应"""
    return not isinstance(exc, urllib.error.HTTPError)


def _stream_chat(messages, max_tokens=2048, temperature=0.3, timeout=None, retries=None,
                 allow_probe=True):
    """调用 OpenAI 兼容 /chat/completions，流式 yield 文本增量。

    timeout 为空时用 CONFIG["timeout"]。意图分类这类短任务必须传入更短的值：
    否则外层线程已按短超时返回，底层 socket 仍会挂满整个长超时周期。
    retries 为空时用 CONFIG["retries"]；短任务应传 0——分类失败直接降级更快。
    allow_probe 为 False 时跳过本机代理探测（探测会串行尝试 8 个端口，短任务等不起）。
    """
    read_timeout = timeout or CONFIG.get("timeout", 25)
    max_retries = int(CONFIG.get("retries", 2)) if retries is None else int(retries)
    url = CONFIG["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": CONFIG["model"],
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % CONFIG["api_key"],
        },
        method="POST",
    )
    resp = None
    last_error = None
    probed = False
    for attempt in range(max_retries + 1):
        try:
            resp = _build_opener().open(req, timeout=read_timeout)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            # 连接失败且未配置代理时，自动探测常见本机代理（一次性）
            if allow_probe and not probed and _is_conn_error(exc) and not (CONFIG.get("proxy") or "").strip():
                probed = True
                p = _probe_proxy()
                if p:
                    try:
                        resp = _build_opener().open(req, timeout=read_timeout)
                        break
                    except Exception:
                        pass
            if attempt >= max_retries:
                raise
            time.sleep(0.35 * (attempt + 1))
    if resp is None:
        raise last_error or RuntimeError("AI 服务暂时不可用")
    with resp:
        buf = b""
        while True:
            data = resp.read(1024)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    return
                try:
                    obj = json.loads(body)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue


def complete(messages, max_tokens=256, temperature=0.0, timeout=8):
    """非流式取回完整回复，用于意图分类这类短输出场景。

    带线程级超时：超时、连接失败、HTTP 错误一律返回 None，
    由调用方决定是否降级，不向上抛异常（分类失败不应影响主流程）。
    """
    buf, errors = [], []

    def run():
        try:
            for chunk in _stream_chat(messages, max_tokens=max_tokens,
                                       temperature=temperature, timeout=timeout,
                                       retries=0, allow_probe=False):
                if chunk:
                    buf.append(chunk)
        except Exception as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout + 0.5)
    if worker.is_alive() or errors:
        return None
    return "".join(buf) or None


def chat_stream(messages, context=None):
    """统一入口：配置了 Key → 真实流式调用；未配置 → 内置智能引擎流式输出"""
    if is_configured():
        full = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        if context:
            full = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "当前分析上下文（数字以此为准）：\n" + context}] + messages
        emitted = False
        try:
            for chunk in _stream_chat(full):
                emitted = True
                yield chunk
        except Exception as exc:
            if emitted:
                yield "\n\n【提示】实时 AI 连接中断，已保留上述响应。确定性计算结果不受影响。"
            else:
                hint = ""
                if isinstance(exc, urllib.error.HTTPError):
                    detail = _http_error_detail(exc)
                    if "quota" in detail.lower():
                        hint = "（原因：API Key 配额不足，请到 platform.openai.com 的 Billing 页面充值）"
                yield "实时 AI 服务暂时无法连接%s，已自动切换为内置智能引擎。\n\n" % hint
                yield from _builtin_engine_stream(messages, context)
    else:
        yield from _builtin_engine_stream(messages, context)


# ---------------- 内置智能引擎（无 Key 兜底） ----------------

def _builtin_engine_stream(messages, context):
    """基于规则与上下文的确定性回答生成，模拟流式输出"""
    if not messages:
        return
    q = messages[-1].get("content", "")
    answer = _builtin_answer(q, context)
    # 按字符流式输出（演示自然）
    step = max(1, len(answer) // 80)
    i = 0
    while i < len(answer):
        yield answer[i:i + step]
        i += step
        time.sleep(0.012)


def _builtin_answer(question, context):
    if not context:
        return ("AI实时接口尚未配置，当前为内置智能引擎模式。\n\n"
                "我可以在接入 OpenAI GPT / DeepSeek 等大模型API后提供实时智能问答。\n\n"
                "请在后端 .env 中配置 CONTRACTGUARD_API_KEY，重启后即可启用实时调用。")
    head = "【内置智能引擎 · 基于当前分析结果】\n\n"
    # 提取上下文中的关键数字形成结构化回答
    lines = [head, "根据当前合同分析结果，为你梳理关键结论：\n"]
    for seg in context.split("\n"):
        seg = seg.strip()
        if seg and (":" in seg or "：" in seg):
            lines.append("- " + seg)
    lines.append("\n如需更深入的自然语言解读，请在后端 .env 中配置 CONTRACTGUARD_API_KEY，重启后即可获得实时大模型分析。")
    return "\n".join(lines)
