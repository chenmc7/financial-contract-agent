/* ContractGuard 产品交互层 */
(function () {
  "use strict";

  const state = {
    contracts: [],
    selected: { agent: new Set(["SC001"]), costlens: new Set(["SC001"]), scenario: new Set(["SC002", "SC003"]), covenant: new Set(["SC004"]), overview: new Set(["SC002", "SC003"]), old: "SC005", newer: "SC006" },
    scenario: "hold",
    aiContext: "",
    aiMessages: [],
    transactionSelections: {},
    demoRunning: false,
    demoSkipped: false,
    lastOverview: null, // 最近一次综合评估结果，打印导出时用于生成报告页眉
    view: "home", // 当前视图，C2 用于把状态同步进地址栏
  };
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "-";
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + " 亿元";
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + " 万元";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " 元";
  };
  const pct = (v, digits = 2) => Number.isFinite(Number(v)) ? (Number(v) * 100).toFixed(digits) + "%" : "-";
  // 缺值（null/undefined/空串）一律显示「—」：0.00% 会被误读成"零成本"，
  // 例如 IRR 无解时 real_annual_cost_pct 为 null，不能回落成 0.00%。
  const rawPct = (v, digits = 2) => (v === null || v === undefined || v === "") ? "—" : (Number.isFinite(Number(v)) ? Number(v).toFixed(digits) + "%" : "—");

  function toast(message, kind = "info") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    $("#toast-wrap").appendChild(node);
    setTimeout(() => node.remove(), 3600);
  }

  function busy(button, on, text = "分析中…") {
    if (!button) return;
    if (on) { button.dataset.label = button.textContent; button.textContent = text; button.disabled = true; }
    else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; }
  }

  /* ══ C2：视图状态进 URL ══
     地址格式：
       #/home
       #/overview?ids=SC002,SC003
       #/diff?old=SC005&new=SC006
     追加 run=1 可在打开后自动运行分析，便于直接把报告链接发给别人。
     视图切换走 pushState（可后退），勾选变化走 replaceState（不污染历史）；
     两者均不触发 hashchange，因此不会与监听互相触发形成循环。 */
  const VIEW_LABELS = { home: "首页", agent: "统一任务 Agent", overview: "一键综合评估", costlens: "真实融资成本分析", scenario: "合同情景比较", covenant: "契约监控预警", diff: "版本风险分析", library: "合同库" };
  const VIEW_RUNNERS = {
    overview: () => runOverview(), costlens: () => runCost(), scenario: () => runScenario(),
    covenant: () => runCovenant(), diff: () => runDiff(), agent: () => runAgentTask(),
  };
  let appliedHash = "";
  let bootApplied = false; // 本页是否已处理过地址栏：run=1 只在首次生效，后退不重复分析

  function buildHash(view) {
    const sel = state.selected;
    if (view === "diff") {
      const q = new URLSearchParams();
      if (sel.old) q.set("old", sel.old);
      if (sel.newer) q.set("new", sel.newer);
      const tail = q.toString();
      return "#/diff" + (tail ? "?" + tail : "");
    }
    const set = sel[view];
    return set instanceof Set && set.size ? `#/${view}?ids=${[...set].join(",")}` : `#/${view}`;
  }

  // 生成「打开即自动出报告」的分享链接，供工具栏复制按钮使用
  function shareUrl(view) {
    const base = location.href.split("#")[0];
    const hash = buildHash(view);
    return base + hash + (hash.includes("?") ? "&" : "?") + "run=1";
  }

  function syncUrl(view, push, keepRun) {
    let next = buildHash(view);
    // 分享链接里的 run=1 在回写时保留，保证刷新后仍能自动出报告
    if (keepRun) next += (next.includes("?") ? "&" : "?") + "run=1";
    if (location.hash === next) return;
    push ? history.pushState(null, "", next) : history.replaceState(null, "", next);
    appliedHash = next;
  }

  function showView(name, opts = {}) {
    state.view = name;
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
    $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
    $("#page-title").textContent = VIEW_LABELS[name] || "ContractGuard";
    $("#view-container").scrollTo({ top: 0, behavior: "smooth" });
    if (!opts.fromUrl) syncUrl(name, !opts.replace);
  }

  // 按地址栏还原视图与勾选；返回是否识别到有效 hash
  function applyFromHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "");
    if (!raw) return false;
    if (location.hash === appliedHash) return true;
    appliedHash = location.hash;

    const [viewRaw, qs] = raw.split("?");
    const view = VIEW_LABELS[viewRaw] ? viewRaw : "home";
    const p = new URLSearchParams(qs || "");
    const known = new Set(state.contracts.map((c) => c.id));

    if (view === "diff") {
      const oldId = p.get("old");
      const newId = p.get("new");
      if (oldId && known.has(oldId)) state.selected.old = oldId;
      if (newId && known.has(newId)) state.selected.newer = newId;
    } else {
      const ids = (p.get("ids") || "").split(",").map((x) => x.trim()).filter((x) => known.has(x));
      if (ids.length && state.selected[view] instanceof Set) state.selected[view] = new Set(ids);
    }

    renderPickers();
    showView(view, { fromUrl: true });
    // 用过滤后的合法状态回写地址，避免分享出去的链接带着已被删除的合同 ID
    const wantRun = p.get("run") === "1";
    syncUrl(view, false, wantRun);

    if (wantRun && VIEW_RUNNERS[view] && !bootApplied) {
      // 留出一帧让选择器渲染完成，再触发分析，避免读到旧的选择状态
      setTimeout(() => { try { VIEW_RUNNERS[view](); } catch (e) { console.warn("自动运行失败", e); } }, 80);
    }
    bootApplied = true;
    return true;
  }

  async function loadContracts() {
    const data = await API.listContracts();
    state.contracts = data.contracts || [];
    const ids = new Set(state.contracts.map((c) => c.id));
    Object.values(state.selected).filter((v) => v instanceof Set).forEach((set) => [...set].forEach((id) => { if (!ids.has(id)) set.delete(id); }));
    renderPickers(); renderLibrary(); updateStats();
  }

  function contractMeta(c) {
    const bits = [];
    bits.push(c.principal ? money(c.principal) : "—");
    bits.push(c.annual_rate != null ? pct(c.annual_rate) : "—");
    bits.push(c.confidence >= .67 ? `<span class="ok">置信度 ${Math.round(c.confidence * 100)}%</span>` : `<span class="miss">需复核 ${Math.round((c.confidence || 0) * 100)}%</span>`);
    return bits.join(" · ");
  }

  function pickerHtml(c, selected, mode) {
    return `<button type="button" class="cp-item ${selected ? (mode === 'single' ? 'single-selected' : 'selected') : ''}" data-id="${esc(c.id)}"><span class="cp-check">${selected ? "✓" : ""}</span><span class="cp-info"><span class="cp-name">${esc(c.name)}</span><span class="cp-meta">${contractMeta(c)}</span></span></button>`;
  }

  function lockDiffPickers() {
    // 同一份合同不能同时作为旧版与新版；仅禁用另一侧已占用的卡片，不改变选择状态。
    [["#diff-old-picker", state.selected.newer], ["#diff-new-picker", state.selected.old]].forEach(([sel, taken]) => {
      $$(sel + " .cp-item").forEach((el) => {
        const locked = !!taken && el.dataset.id === taken;
        el.disabled = locked;
        el.classList.toggle("cp-locked", locked);
        if (locked) el.title = "已作为另一侧版本选中";
      });
    });
  }

  function renderPicker(selector, key, mode = "multi") {
    const root = $(selector); if (!root) return;
    root.innerHTML = state.contracts.map((c) => pickerHtml(c, mode === "single" ? state.selected[key] === c.id : state.selected[key].has(c.id), mode)).join("");
    root.onclick = (e) => {
      const item = e.target.closest(".cp-item"); if (!item) return;
      const id = item.dataset.id;
      if (mode === "single") state.selected[key] = id;
      else state.selected[key].has(id) ? state.selected[key].delete(id) : state.selected[key].add(id);
      renderPicker(selector, key, mode);
      if (key === "old" || key === "newer") lockDiffPickers();
      syncUrl(state.view, false); // 勾选变化只改写当前历史条目，避免每次勾选都新增一条后退记录
    };
  }

  function renderPickers() {
    renderPicker("#costlens-picker", "costlens");
    renderPicker("#agent-picker", "agent");
    renderPicker("#scenario-picker", "scenario");
    renderPicker("#covenant-picker", "covenant");
    renderPicker("#overview-picker", "overview");
    renderPicker("#diff-old-picker", "old", "single");
    renderPicker("#diff-new-picker", "newer", "single");
    lockDiffPickers();
  }

  function updateStats() {
    $("#stat-contracts").textContent = state.contracts.length;
    $("#stat-clauses").textContent = state.contracts.reduce((n, c) => n + (c.clause_count || 0), 0);
    $("#stat-monitor").textContent = state.contracts.reduce((n, c) => n + (c.covenants || []).length, 0);
  }

  function renderLibrary() {
    $("#lib-list").innerHTML = state.contracts.map((c) => {
      const fs = c.field_status || {};
      const rateName = c.type_key === "wealth" ? "收益/费率" : c.type_key === "securities" ? "参考费率" : "利率";
      const rateText = fs.rate === "not_applicable" ? "利率不适用" : c.annual_rate != null ? `${rateName} ${pct(c.annual_rate)}` : fs.rate === "uncertain" ? "利率待复核" : "利率未发现";
      const repayText = fs.payment_method === "not_applicable" ? "还款方式不适用" : fs.payment_method === "uncertain" ? "还款方式待复核" : c.repayment || "还款方式未发现";
      return `<article class="lib-item" data-id="${esc(c.id)}"><div class="lib-icon">📄</div><div class="lib-main"><div class="lib-name-row"><div class="lib-name">${esc(c.name)}</div><button class="rename-btn" type="button" data-rename="${esc(c.id)}">重命名</button></div><div class="lib-desc">${esc(c.type_label || c.type)} · 判定 ${Math.round((c.type_confidence || 0) * 100)}% · ${c.word_count || 0} 字 · ${c.clause_count || 0} 个条款</div><div class="lib-badges"><span class="badge badge-blue">${esc(rateText)}</span><span class="badge badge-gray">${esc(repayText)}</span>${(c.covenants || []).length ? '<span class="badge badge-amber">含财务契约</span>' : ''}</div></div><div class="lib-tags"><div class="lib-amt">${c.principal ? money(c.principal).replace(" ", "<small>") + "</small>" : "-"}</div>${c.source !== "builtin" ? `<button class="lib-del" type="button" data-delete="${esc(c.id)}">删除</button>` : '<span class="badge badge-green">内置样例</span>'}</div></article>`;
    }).join("");
  }

  /* ---------- 结构化解析面板 ---------- */
  const PIPE_STAGES = [
    ["normalize", "文本清洗"], ["classify", "类型识别"], ["universal", "通用抽取"],
    ["extend", "专属扩展"], ["standardize", "字段标准化"], ["evidence", "证据绑定"], ["llm", "LLM补强"],
  ];

  function llmState(pipe) {
    const s = (pipe && pipe.llm) || "skipped";
    if (s === "llm_ok") return { cls: "ok", text: "已补强" };
    if (s === "llm_no_verified_evidence") return { cls: "warn", text: "无可验证补强" };
    if (s === "llm_off") return { cls: "off", text: "内置引擎" };
    if (s === "no_gap") return { cls: "off", text: "无需补强" };
    if (s === "llm_fail" || s === "llm_error" || s === "timeout" || s.startsWith("llm_fail:")) return { cls: "warn", text: "已降级" };
    return { cls: "off", text: "未启用" };
  }

  function confBadge(f) {
    const conf = f.confidence || 0, p = Math.round(conf * 100);
    const st = f.status || (f.found ? "confirmed" : "not_found");
    if (st === "conflict") return '<span class="fconf fconf-conflict">冲突 · 双源不一致</span>';
    if (st === "not_applicable") return '<span class="fconf fconf-na">不适用</span>';
    if (st === "not_found") return '<span class="fconf fconf-miss">未发现</span>';
    if (st === "uncertain") return '<span class="fconf fconf-low">待复核 ' + p + "%</span>";
    if (st === "confirmed_by_user") return '<span class="fconf fconf-user">用户已确认</span>';
    if (conf >= 0.85) return '<span class="fconf fconf-high">高 ' + p + "%</span>";
    if (conf >= 0.7) return '<span class="fconf fconf-mid">中 ' + p + "%</span>";
    return '<span class="fconf fconf-low">低 ' + p + "% · 复核</span>";
  }

  const FIELD_STATE_HINT = {
    not_applicable: "该字段不适用于当前合同类型（如担保合同无利率条款），系统已标注而非报错",
    not_found: "原文未发现该字段，分析在缺项下继续，不阻断其他模块",
    uncertain: "候选内容置信度低于阈值，需人工复核后采信",
    conflict: "跨条款多处表述不一致，请核对全部原文出处",
  };

  function frowHtml(f) {
    const clause = f.clause || "";
    const ev = (f.source_text || "").trim();
    const st = f.status || (f.found ? "confirmed" : "not_found");
    const hint = FIELD_STATE_HINT[st];
    const noteTag = (f.note || hint) ? ' <i class="f-note" title="' + esc(f.note || hint || "") + '">ⓘ</i>' : "";
    const valText = st === "not_applicable" ? "不适用" : (f.display || "未发现");
    const evText = st === "not_applicable" ? "—" : ev;
    const span = f.evidence_span || {};
    const spanAttrs = span.start != null ? ` data-evidence-start="${esc(span.start)}" data-evidence-end="${esc(span.end)}" data-paragraph-id="${esc(span.paragraph_id || "")}"` : "";
    const editable = ["amount","currency","rate","term","parties","payment_method","guarantee",
      "actual_financing_amount","credit_limit","credit_term","single_financing_term",
      "financing_party","factoring_bank","baseline_date","financial_statement_date"].includes(f.key);
    return '<div class="frow frow-' + st + '" data-field="' + esc(f.key) + '"' + spanAttrs + (clause ? ' data-clause="' + esc(clause) + '" title="点击证据定位原文"' : "") + ">"
      + '<span class="f-name">' + esc(f.label) + noteTag + "</span>"
      + '<span class="f-val">' + esc(valText) + "</span>"
      + '<span class="f-ev">' + (evText ? esc(evText) : '<i class="f-ev-empty">' + (st === "not_applicable" ? "—" : "无原文证据") + "</i>") + "</span>"
      + '<span class="f-clause">' + (clause ? esc(clause) + (f.page ? " · P" + f.page : "") : "—") + "</span>"
      + '<span class="f-conf">' + confBadge(f) + (editable ? '<button class="field-edit-btn" type="button" data-edit-field="' + esc(f.key) + '">复核</button>' : '') + "</span>"
      + "</div>";
  }

  function fieldTableHtml(fields) {
    const groups = new Map();
    Object.values(fields || {}).forEach((f) => {
      if (!groups.has(f.group)) groups.set(f.group, []);
      groups.get(f.group).push(f);
    });
    if (!groups.size) return "";
    const base = ["基本信息", "核心交易", "风险条款"];
    const names = [...groups.keys()];
    const specific = names.filter((n) => base.indexOf(n) < 0 && n !== "通用条款");
    const seq = base.filter((n) => groups.has(n)).concat(specific);
    if (groups.has("通用条款")) seq.push("通用条款");
    let html = '<div class="field-table">';
    seq.forEach((g) => {
      html += '<div class="f-group">' + esc(g) + "</div>";
      groups.get(g).forEach((f) => { html += frowHtml(f); });
    });
    return html + "</div>";
  }

  function documentHtml(text, documentIndex) {
    const paragraphs = (documentIndex && documentIndex.paragraphs) || [];
    const lines = String(text || "").split("\n");
    let cursor = 0, page = 1, pIndex = 0, html = "", opened = false, pageHasContent = false;
    const openPage = (n) => {
      if (opened) html += "</div>";
      page = n || page;
      html += `<div class="document-page" data-page="${esc(page)}"><div class="document-page-label">第 ${esc(page)} 页</div>`;
      opened = true; pageHasContent = false;
    };
    openPage(1);
    lines.forEach((line) => {
      const marker = line.match(/^\s*【\s*第\s*(\d+)\s*页\s*】\s*$/);
      if (marker) {
        const markerPage = +marker[1];
        // 页码标记常在第一页文本之前出现；避免已经打开的第 1 页重复渲染页眉。
        if (!(markerPage === page && !pageHasContent)) openPage(markerPage);
        cursor += line.length + 1; return;
      }
      const body = line.replace(/\r$/, "");
      if (!body.trim()) { html += '<div class="document-blank" aria-hidden="true"></div>'; cursor += line.length + 1; return; }
      const info = paragraphs[pIndex++] || { paragraph_id: `p${String(pIndex).padStart(4, "0")}`, page, start: cursor, end: cursor + body.length };
      html += `<p class="document-paragraph" data-page="${esc(info.page || page)}" data-paragraph-id="${esc(info.paragraph_id || "")}" data-start="${esc(info.start)}" data-end="${esc(info.end)}">${esc(body)}</p>`;
      pageHasContent = true;
      cursor += line.length + 1;
    });
    if (opened) html += "</div>";
    return html;
  }

  function transactionHtml(transactions, documentType, transactionType, fallbackAmount) {
    const list = transactions || [];
    if (!list.length) return '<div class="transaction-panel"><span class="hint-text">未检测到可独立归属的融资交易。</span></div>';
    const selected = state.transactionSelections[state.currentContract] || list[0].transaction_id;
    const options = list.map((t) => {
      const amountValue = t.primary && t.primary.amount ? t.primary.amount.value : fallbackAmount;
      const amount = amountValue != null ? money(amountValue) : "金额待复核";
      const term = t.term_change ? `${t.term_change.from} → ${t.term_change.to} 个月` : t.primary && t.primary.term && t.primary.term.value ? `${t.primary.term.value} 个月` : "期限待复核";
      return `<option value="${esc(t.transaction_id)}" ${t.transaction_id === selected ? "selected" : ""}>${esc(t.label || t.transaction_id)} · ${esc(amount)} · ${esc(term)}</option>`;
    }).join("");
    return `<div class="transaction-panel"><div class="transaction-head"><b>检测到 ${list.length} 笔交易</b><span>${esc(documentType || "其他金融文件")} · ${esc(transactionType || "其他")}</span></div><label>当前交易<select id="transaction-select">${options}</select></label><div id="transaction-detail" class="transaction-detail"></div></div>`;
  }

  function transactionDetailHtml(t) {
    if (!t) return "";
    const uniq = (items) => { const seen = new Set(); return (items || []).filter((x) => { const k = `${x.role || x.label}|${x.value ?? "uncertain"}`; if (seen.has(k)) return false; seen.add(k); return true; }); };
    const amountRows = uniq(t.amounts).map((x) => `<span>${esc(x.label)}：<b>${esc(money(x.value))}</b></span>`).join("");
    const termRows = uniq(t.terms).map((x) => `<span>${esc(x.label)}：<b>${esc(x.value == null ? "上限/待复核" : x.value + " 个月")}</b></span>`).join("");
    const guarantees = [...new Set((t.guarantee || []).map((x) => x.value))].filter(Boolean).join("、") || "未识别";
    return `<div class="transaction-facts"><div><b>金额</b>${amountRows || "<span>未发现</span>"}</div><div><b>期限</b>${t.term_change ? `<span><b>${esc(t.term_change.from)} → ${esc(t.term_change.to)} 个月</b>（补充协议变更）</span>` : termRows || "<span>未发现</span>"}</div><div><b>担保/责任</b><span>${esc(guarantees)}</span></div></div>`;
  }

  function bindEvidence(row) {
    const view = document.getElementById("clause-view"), start = Number(row.dataset.evidenceStart), end = Number(row.dataset.evidenceEnd);
    if (!view) return;
    let target = Number.isFinite(start) ? $$(".document-paragraph", view).find((p) => Number(p.dataset.start) <= start && Number(p.dataset.end) >= start) : null;
    if (!target && row.dataset.paragraphId) target = $$(".document-paragraph", view).find((p) => p.dataset.paragraphId === row.dataset.paragraphId);
    if (!target && row.dataset.clause) target = $$(".document-paragraph", view).find((p) => p.textContent.includes(row.dataset.clause));
    if (!target) return;
    $$("mark.evidence-hit", view).forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
    $$(".document-paragraph.flash", view).forEach((p) => p.classList.remove("flash"));
    target.classList.add("flash");
    if (Number.isFinite(start) && Number.isFinite(end)) {
      const from = Math.max(0, start - Number(target.dataset.start));
      const to = Math.min(target.textContent.length, end - Number(target.dataset.start));
      if (to > from) {
        const range = document.createRange(), node = target.firstChild;
        range.setStart(node, from); range.setEnd(node, to);
        const mark = document.createElement("mark"); mark.className = "evidence-hit";
        range.surroundContents(mark);
      }
    }
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function pipelineHtml(pipe, wordCount) {
    const chips = PIPE_STAGES.map(([k, label]) => {
      if (k === "llm") {
        const st = llmState(pipe);
        return '<span class="pl-chip pl-' + st.cls + '" title="LLM 语义补强：' + st.text + '">' + label + " · " + st.text + "</span>";
      }
      return '<span class="pl-chip pl-done">' + label + "</span>";
    }).join('<span class="pl-sep">›</span>');
    const meta = pipe ? (pipe.sections || 0) + " 个条款 · " + (pipe.pages || 1) + " 页 · " + (wordCount || 0) + " 字" : "";
    return '<div class="parse-pipeline">' + chips + (meta ? '<span class="pl-meta">' + meta + "</span>" : "") + "</div>";
  }

  /* ---------- 左右分栏 · 事实层与 AI 分析层 ---------- */

  function layerTitle(text, layer, extra) {
    return '<div class="cd-sec-title"><span class="layer-tag ' + layer + '">' + (layer === "fact" ? "事实" : "AI 分析") + "</span>" + esc(text) + (extra || "") + "</div>";
  }

  function planStepsHtml(orch) {
    const plan = (orch && orch.plan) || {};
    const steps = plan.steps || [];
    if (!steps.length) return "";
    const STATUS = {
      run: { cls: "st-run", icon: "●", label: "执行" },
      degraded: { cls: "st-deg", icon: "◐", label: "降级" },
      skip: { cls: "st-skip", icon: "○", label: "跳过" },
    };
    const rows = steps.map((s) => {
      const st = STATUS[s.status] || STATUS.skip;
      const detail = (s.detail || []).length ? '<ul class="step-detail">' + s.detail.map((d) => "<li>" + esc(d) + "</li>").join("") + "</ul>" : "";
      return '<div class="step-row ' + st.cls + '">'
        + '<span class="step-ic" title="' + st.label + '">' + st.icon + "</span>"
        + '<div class="step-main"><div class="step-head"><b>' + esc(s.label) + '</b><span class="step-status">' + st.label + "</span></div>"
        + '<p class="step-reason">' + esc(s.reason || "") + "</p>" + detail + "</div></div>";
    }).join("");
    return '<div class="plan-steps">' + rows + "</div>"
      + (plan.advice ? '<div class="plan-advice"><b>Agent 建议</b>' + esc(plan.advice) + "</div>" : "");
  }

  function riskCardsHtml(orch) {
    const risks = (orch && orch.risks) || [];
    if (!risks.length) return '<div class="hint-text">规则引擎未在该合同中识别出需提示的风险条目。</div>';
    const LEVEL = { "高": "badge-red", "中": "badge-amber", "低": "badge-gray" };
    const CAT_COLOR = { economic: "eco", credit: "cre", guarantee: "gua", covenant: "cov", compliance: "com", operation: "ops" };
    return risks.map((r) => {
      const legal = r.needs_legal_review ? '<div class="risk-legal"><b>⚖ 法律效力提示</b>该条判断涉及法律效力认定，系统结论仅供参考，<b>需要专业法律人员复核</b>。</div>' : "";
      const span = r.evidence_span || {};
      const spanAttrs = span.start != null ? ` data-evidence-start="${esc(span.start)}" data-evidence-end="${esc(span.end)}" data-paragraph-id="${esc(span.paragraph_id || "")}"` : "";
      return '<article class="risk-card lv-' + (r.level === "高" ? "high" : r.level === "中" ? "mid" : "low") + '">'
        + '<div class="risk-head"><span class="risk-cat ' + (CAT_COLOR[r.category] || "") + '">' + esc(r.category_label || r.category) + "</span>"
        + '<b class="risk-title">' + esc(r.title) + "</b>"
        + '<span class="badge ' + (LEVEL[r.level] || "badge-gray") + '">' + esc(r.level) + "风险</span>"
        + '<span class="risk-conf" title="规则引擎推导置信度">置信度 ' + Math.round((r.confidence || 0) * 100) + "%</span></div>"
        + '<div class="risk-meta"><span>影响主体：' + esc(r.affected_party || "—") + "</span>"
        + (r.clause ? "<span>条款位置：" + esc(r.clause) + (r.page ? " · P" + r.page : "") + "</span>" : "")
        + '<span class="risk-engine">规则引擎推导</span></div>'
        + (r.source_text ? '<div class="risk-src risk-evidence"' + spanAttrs + ' title="点击定位原文">原文证据：<mark>' + esc(r.source_text) + "</mark></div>" : "")
        + '<div class="risk-body"><p><b>风险解释：</b>' + esc(r.explanation || "") + "</p>"
        + '<p><b>可能后果：</b>' + esc(r.consequence || "") + "</p>"
        + '<p><b>复核 / 修改建议：</b>' + esc(r.suggestion || "") + "</p></div>" + legal + "</article>";
    }).join("");
  }

  function conflictsHtml(orch) {
    const conflicts = (orch && orch.consistency && orch.consistency.conflicts) || [];
    if (!conflicts.length) return '<div class="hint-text">跨条款一致性检查通过：金额、利率、期限、主体、违约责任均未发现相互矛盾。</div>';
    const SEV = { high: "badge-red", medium: "badge-amber", low: "badge-gray" };
    return conflicts.map((c) => {
      const evs = (c.evidences || []).map((e) => '<div class="cf-ev"><span class="cf-ev-val">' + esc(e.value ?? "—") + '</span><span class="cf-ev-clause">' + esc(e.clause || "未定位条款") + '</span><span class="cf-ev-text">' + esc(e.text || "") + "</span></div>").join("");
      return '<article class="conflict-card"><div class="cf-head"><span class="badge ' + (SEV[c.severity] || "badge-amber") + '">' + (c.severity === "high" ? "高" : c.severity === "medium" ? "中" : "低") + '风险</span><b>' + esc(c.title) + '</b><span class="cf-check">检查项：' + esc(c.checked || c.type) + "</span></div>"
        + '<p class="cf-explain">' + esc(c.explanation || "") + "</p>"
        + '<div class="cf-evs"><div class="cf-evs-title">双方原文证据对照</div>' + evs + "</div></article>";
    }).join("");
  }

  function diagnosticsHtml(orch) {
    const d = (orch && orch.diagnostics) || {};
    if (!d || !d.text) return "";
    const fs = d.field_stats || {};
    const chips = [
      ["confirmed", "已确认"], ["uncertain", "待复核"], ["not_found", "未发现"], ["not_applicable", "不适用"], ["conflict", "冲突"],
    ].map(([k, label]) => '<span class="diag-chip dc-' + k + '"><b>' + (fs[k] ?? 0) + "</b>" + label + "</span>").join("");
    return '<div class="diag-stats">' + chips + "</div>"
      + '<ul class="diag-lines">' + (d.text || []).map((l) => "<li>" + esc(l) + "</li>").join("") + "</ul>";
  }

  const PARSE_STATUS_TEXT = { success: "解析成功", needs_text_version: "需文本版", failed: "解析失败", error: "解析失败" };

  function fileDiagnosticsHtml(d) {
    if (!d || !Object.keys(d).length) return "";
    const fmt = (d.format || "text").toUpperCase();
    const posRaw = d.pages == null ? (d.page_note || "使用文本位置") : `${d.pages} 页`;
    // 页注已带格式名时去掉重复前缀，避免出现“DOCX · DOCX …”
    const pos = d.pages == null && posRaw.toUpperCase().startsWith(fmt)
      ? posRaw.slice(fmt.length).replace(/^[\s·:：-]+/, "") : posRaw;
    const statusText = PARSE_STATUS_TEXT[d.status] || d.status || "解析成功";
    const warns = (d.warnings || []).map((x) => `<span class="diag-warning">${esc(x)}</span>`).join("");
    return `<div class="file-diagnostics"><b>文件解析状态</b><span class="badge badge-green">${esc(statusText)}</span><span>${esc(fmt)}</span><span>${esc(pos)}</span><span>OCR ${d.ocr_page_count || 0} 页</span><span>表格 ${d.table_count || 0}</span><span>低质量页 ${(d.low_quality_pages || []).length}</span><span>敏感信息命中 ${d.sensitive_matches || 0}</span>${warns}</div>`;
  }

  function reviewHtml(orch, reviewStatus = {}) {
    const items = (orch && orch.review) || [];
    if (!items.length) return '<div class="hint-text">本轮解析无待复核项：字段置信度、类型判定与冲突检查均达阈值。</div>';
    const TYPE_ICON = { uncertain_field: "◐", conflict_field: "⨯", low_type_confidence: "◇", legal_review: "⚖" };
    return '<ul class="review-list">' + items.map((it, i) => `<li class="rv-${esc(it.type)}" data-review-id="${esc(it.source || (`review-${i}`))}"><span class="rv-ic">${TYPE_ICON[it.type] || "•"}</span><div><b>${esc(it.label)}</b><p>${esc(it.text)}</p><div class="review-actions"><button data-review-status="submitted_legal">已提交法务</button><button data-review-status="confirmed">已确认</button><button data-review-status="accepted_risk">接受风险</button></div></div></li>`).join("") + "</ul>";
  }

  async function showContract(id) {
    try {
      const data = await API.getContract(id), c = data.contract;
      state.currentContract = id;
      state.currentDetail = data;
      const orch = data.orchestration || {};
      const fields = c.fields || {}, rf = (k, fallback, max) => {
        const d = fields[k] && fields[k].display;
        if (!d) return fallback || "-";
        return d.length > (max || 22) ? d.slice(0, max || 22) + "…" : d;
      };
      const transactions = c.transactions || data.transactions || [];
      if (transactions.length && !state.transactionSelections[id]) state.transactionSelections[id] = transactions[0].transaction_id;
      const fc = c.field_count || {};
      const head = '<section class="contract-detail">'
        + '<div class="cd-head"><div class="cd-title"><h3>' + esc(c.name) + "</h3><p>"
        + "解析置信度 " + Math.round((c.confidence || 0) * 100) + "%"
        + (fc.total ? " · 已识别 " + fc.found + "/" + fc.total + " 个字段（缺失字段不阻断分析）" : "")
        + " · 分析版本 V" + (c.analysis_version || 1) + " · 所有结果建议结合原文复核</p></div>"
        + '<div class="cd-actions"><button class="btn btn-ghost btn-sm" data-rename-detail="' + esc(c.id) + '">重命名</button><button class="btn btn-ghost btn-sm" data-reparse>重新解析</button><a class="btn btn-ghost btn-sm" href="/api/contracts/' + esc(c.id) + '/export" target="_blank">导出</a><button class="btn btn-ghost btn-sm" data-close-detail>收起</button></div></div>'
        + '<div class="cd-typebar"><span class="cd-type-badge">' + esc(c.type_label || c.type) + "</span>"
        + '<span class="cd-type-conf">类型判定 ' + Math.round((c.type_confidence || 0) * 100) + "%</span>"
        + '<span class="cd-type-ev" title="' + esc(c.type_evidence || "") + '">' + esc(c.type_evidence || "") + "</span></div>"
        + pipelineHtml(c.pipeline, c.word_count)
        + fileDiagnosticsHtml(c.file_diagnostics || {})
        + '<div class="cd-split">'
        /* ── 左栏：合同原文（唯一事实来源）── */
        + '<div class="cd-left">'
        + '<div class="cd-pane-title"><span class="layer-tag fact">事实</span>合同原文<span class="cd-pane-sub">点击右侧字段可定位条款</span></div>'
        + '<div class="clause-view" id="clause-view">'
        + documentHtml(data.text || "", c.document || data.document)
        + "</div></div>"
        /* ── 右栏：结构化分析 ── */
        + '<div class="cd-right">'
        + transactionHtml(transactions, c.document_type || c.fields?.document_type?.display, c.transaction_type || c.fields?.transaction_type?.display, c.principal)
        /* 1. 合同核心信息（事实层） */
        + layerTitle("合同核心信息 · 字段证据绑定", "fact")
        + '<div class="term-grid">'
        + '<div class="term-item"><label>合同金额</label><b>' + esc(rf("amount", money(c.principal), 26)) + "</b></div>"
        + '<div class="term-item"><label>利率/费率</label><b>' + esc(rf("rate", pct(c.annual_rate), 26)) + "</b></div>"
        + '<div class="term-item"><label>期限</label><b>' + esc(rf("term", c.term_months ? c.term_months + " 个月" : "-", 26)) + "</b></div>"
        + '<div class="term-item"><label>付款/还款方式</label><b>' + esc(rf("payment_method", c.repayment, 26)) + "</b></div>"
        + '<div class="term-item"><label>担保安排</label><b class="alert">' + esc(rf("guarantee", c.guarantee, 26)) + "</b></div></div>"
        + '<details class="cd-fold"><summary>查看完整字段列表、状态与证据</summary>' + fieldTableHtml(fields) + '</details>'
        + '<div class="cd-sec-title">费用条款</div><div class="fee-list">'
        + ((c.fees || []).length ? c.fees.map((f) => '<div class="fee-row"><span class="fr-name">' + esc(f.name) + '</span><span class="fr-rate">' + pct(f.rate) + '</span><span class="fr-amt">' + money(f.amount) + '</span><span class="fr-src">' + esc(f.clause || f.source || "") + "</span></div>").join("") : '<span class="hint-text">未识别到固定费用，请人工核对附件与补充协议。</span>')
        + "</div>"
        /* 2. AI 风险分析（分析层） */
        + layerTitle("风险识别 · 六类风险卡片", "ai")
        + riskCardsHtml(orch)
        /* 3. 人工复核清单 */
        + layerTitle("人工复核清单", "ai")
        + reviewHtml(orch, data.review_status || {})
        /* 4. Agent 与技术信息按需展开 */
        + '<details class="cd-fold"><summary>查看 Agent 调度过程与分析依据</summary>'
        + layerTitle("Agent 调度过程 · 按合同内容自动编排", "ai")
        + planStepsHtml(orch)
        + layerTitle("跨条款一致性 · 冲突对照", "ai")
        + conflictsHtml(orch)
        + layerTitle("解析诊断 · 质量面板", "ai")
        + diagnosticsHtml(orch)
        + '</details>'
        + "</div></div></section>";
      $("#contract-detail").innerHTML = head;
      const txSelect = document.getElementById("transaction-select");
      const selectedTx = transactions.find((t) => t.transaction_id === (state.transactionSelections[id] || transactions[0]?.transaction_id));
      const txDetail = document.getElementById("transaction-detail");
      if (txDetail) txDetail.innerHTML = transactionDetailHtml(selectedTx);
      if (txSelect) txSelect.onchange = () => { state.transactionSelections[id] = txSelect.value; const tx = transactions.find((t) => t.transaction_id === txSelect.value); const detail = document.getElementById("transaction-detail"); if (detail) detail.innerHTML = transactionDetailHtml(tx); toast("已切换交易；成本测算将只使用当前交易字段", "info"); };
      Object.entries(data.review_status || {}).forEach(([id, saved]) => {
        const row = $$("[data-review-id]", $("#contract-detail")).find((x) => x.dataset.reviewId === id);
        if (row && saved.status) { row.classList.add("review-done"); const note = document.createElement("small"); note.className = "review-saved"; note.textContent = `已保存：${saved.status}`; row.querySelector("div > div")?.appendChild(note); }
      });
      $("#contract-detail").scrollIntoView({ behavior: "smooth", block: "start" });
      $$("#contract-detail .frow[data-clause]").forEach((row) => row.addEventListener("click", () => bindEvidence(row)));
    } catch (e) { toast(e.message, "err"); }
  }

  function loading(root, text) { root.innerHTML = `<div class="loading-block"><div class="spinner"></div>${esc(text)}</div>`; }

  async function runCost() {
    const ids = [...state.selected.costlens], button = $("#btn-costlens-run"), root = $("#costlens-results");
    if (!ids.length) return toast("请先选择至少一份合同", "err");
    busy(button, true); loading(root, "正在重构现金流并计算真实融资成本…");
    try {
      const selected = Object.fromEntries(ids.filter((id) => state.transactionSelections[id]).map((id) => [id, state.transactionSelections[id]]));
      const data = await API.costlens(ids, selected);
      root.innerHTML = data.analyses.map(renderCost).join("");
      state.aiContext = data.analyses.map((a) => `${a.name}：${a.result.summary || a.result.reason || "无成本结论"}`).join("\n");
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }

  function renderCost(a) {
    const r = a.result;
    if (r.hidden) {
      const gaps = (r.missing_detail || []).map((m) => `<li><b>${esc(m.label)}</b>（${esc({ not_found: "原文未发现", not_applicable: "类型不适用", uncertain: "待人工复核" }[m.status] || m.status)}）：${esc(m.text)}</li>`).join("");
      return `<div class="result-block"><div class="result-title">${esc(a.name)}</div><div class="summary-banner"><div class="sb-icon">ⓘ</div><div class="sb-body"><b>CostLens 已自动隐藏</b><p>${esc(r.reason)}</p>${gaps ? `<ul class="missing-detail">${gaps}</ul>` : ""}</div></div></div>`;
    }
    if (r.error) return `<div class="result-block"><div class="result-title">${esc(a.name)}</div><div class="summary-banner red"><div class="sb-icon">⚠</div><div class="sb-body"><b>暂时无法计算</b><p>${esc(r.error)}。请在原文中复核金额、利率和期限。</p></div></div></div>`;
    const items = (r.cost_items || []).map((x) => ({ label: x.name, value: x.amount, display: money(x.amount) }));
    const flows = (r.cashflows || []).filter((_, i, arr) => i === 0 || i === arr.length - 1 || i % Math.max(1, Math.floor(arr.length / 5)) === 0).map((f) => ({ label: `第${f.period}期`, value: f.total, display: money(f.total) }));
    const statusLabels = { confirmed: "已确认", confirmed_by_user: "用户已确认", uncertain: "待复核", not_found: "未发现", not_applicable: "不适用", conflict: "存在冲突" };
    const meaningfulItems = items.filter((x) => Number(x.value) > 0);
    const showCostChart = meaningfulItems.length > 1 && new Set(meaningfulItems.map((x) => Number(x.value).toFixed(2))).size > 1;
    const showFlowChart = flows.length > 1 && new Set(flows.map((x) => Number(x.value).toFixed(2))).size > 1;
    const costChart = showCostChart ? CGCharts.bars(meaningfulItems, { label: "成本项目对比" }) : "";
    const flowChart = showFlowChart ? CGCharts.line(flows, { label: "还款现金流" }) : '<div class="hint-text">各展示期还款金额一致，已省略无变化曲线。</div>';
    const feeTrend = Number(r.rate_gap_pp) > 0 ? `<div class="k-trend up">固定费用占本金 +${rawPct(r.rate_gap_pp, 2).replace('%','')} pp</div>` : '<div class="k-sub">未识别到固定费用</div>';
    const inputRows = (r.inputs || []).map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(x.normalized ?? "待复核")}</td><td>${esc(x.source_text || "未定位")}</td><td>${esc(x.clause || (x.page ? `第${x.page}页` : "文本位置"))}</td><td>${esc(x.formula_role)}</td><td><span class="status-badge st-${esc(x.status)}">${esc(statusLabels[x.status] || "未知状态")}</span></td></tr>`).join("");
    return `<section class="result-block"><div class="result-title">${esc(a.name)}<span class="rt-tag">Cost Lens</span></div><div class="kpi-grid"><div class="kpi"><div class="k-label">名义年利率</div><div class="k-value">${pct(r.annual_rate)}</div><div class="k-sub">合同表面利率</div></div><div class="kpi"><div class="k-label">期限内总成本率</div><div class="k-value hl">${rawPct(r.comprehensive_rate_pct)}</div>${feeTrend}<div class="k-sub">（总利息 + 固定费用）/ 本金，非年化口径</div></div><div class="kpi"><div class="k-label">真实年化成本</div><div class="k-value warn">${rawPct(r.real_annual_cost_pct)}</div><div class="k-sub">${r.real_annual_cost_pct === null || r.real_annual_cost_pct === undefined ? esc(r.real_annual_cost_note || "IRR 无解") : "基于净到手资金的 IRR"}</div></div><div class="kpi"><div class="k-label">总融资成本</div><div class="k-value">${money(r.total_cost)}</div><div class="k-sub">其中费用 ${money(r.total_fees)}</div></div></div><div class="summary-banner gold"><div class="sb-icon">✦</div><div class="sb-body"><b>确定性引擎结论</b><p>${esc(r.summary)}</p></div></div><div class="result-title">计算输入与可复核证据</div><table class="data-table"><thead><tr><th>输入</th><th>标准化值</th><th>原文证据</th><th>位置</th><th>计算用途</th><th>状态</th></tr></thead><tbody>${inputRows}</tbody></table><div class="formula-box">R<sub>cost</sub> = <span class="fx-var">(I + F + P) / N</span><span class="fx-note">本案 = (${money(r.total_interest)} + ${money(r.total_fees)} + 0) / ${money(r.principal)}</span></div><div class="result-title">成本拆解与原文证据</div><table class="data-table"><thead><tr><th>项目</th><th>金额</th><th>计算或条款位置</th></tr></thead><tbody>${(r.cost_items || []).map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${money(x.amount)}</td><td>${esc(x.detail || x.clause || "")}</td></tr>`).join("")}</tbody></table>${costChart}<div class="result-title">还款现金流节点</div>${flowChart}</section>`;
  }

  async function runScenario() {
    const ids = [...state.selected.scenario], button = $("#btn-scenario-run"), root = $("#scenario-results");
    if (ids.length < 1) return toast("请先选择合同", "err");
    const transaction_ids = Object.fromEntries(ids.filter((id) => state.transactionSelections[id]).map((id) => [id, state.transactionSelections[id]]));
    const body = { contract_ids: ids, scenario: state.scenario, month: +$("#param-month").value, delta_pp: +$("#param-delta").value, months_late: +$("#param-late").value, compare_all: $("#param-flip").checked && ids.length >= 2, transaction_ids };
    busy(button, true, "模拟中…"); loading(root, "正在将同一组经营假设应用到所选合同…");
    try {
      const data = await API.scenario(body);
      const SC_LABELS = { hold: "持有到期", prepay: "提前还款", rate_up: "利率上升", stress: "现金流紧张", overdue: "逾期" };
      if (data.mode === "blocked") {
        const details = (data.contracts || []).flatMap((c) => (c.missing_detail || []).map((m) => `<li><b>${esc(c.name)}</b>：${esc(m.text)}</li>`)).join("");
        root.innerHTML = `<div class="result-block"><div class="summary-banner"><div class="sb-icon">ⓘ</div><div class="sb-body"><b>情景模拟已自动隐藏</b><p>${esc(data.reason || "当前信息不足，暂无法可靠模拟")}</p><ul class="missing-detail">${details}</ul></div></div></div>`;
        return;
      }
      root.innerHTML = data.mode === "flip" ? renderFlip(data) : renderCompare(data.results, SC_LABELS[state.scenario] || "情景模拟", false);
      state.aiContext = data.mode === "flip" ? `持有到期最优：${data.hold[0].name}，成本${money(data.hold[0].total_cost)}\n第${data.prepay_month}个月提前还款最优：${data.prepay[0].name}，成本${money(data.prepay[0].total_cost)}\n排名翻转：${data.flips.map((x) => x.note).join('；')}` : data.results.map((x) => `${x.name}：${money(x.total_cost)}，排名${x.rank}`).join("\n");
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }

  const SC_METRICS = [
    { title: "综合排名", key: "rank", fmt: (r) => `第 ${r.rank} 名`, lower: true, strong: true },
    { title: "总融资成本", key: "total_cost", fmt: (r) => money(r.total_cost), lower: true, strong: true },
    { title: "利息支出", key: "interest", fmt: (r) => money(r.interest), lower: true },
    { title: "固定费用", key: "fees", fmt: (r) => money(r.fees || 0), lower: true },
    { title: "违约金 / 罚息", key: "penalty", fmt: (r) => money(r.penalty || 0), lower: true },
    { title: "与最优差额", key: "diff_vs_best", fmt: (r) => (+r.diff_vs_best ? "+" + money(r.diff_vs_best) : "基准"), lower: true },
    { title: "名义年利率", key: "annual_rate", fmt: (r) => pct(r.annual_rate), lower: true },
    { title: "提前还款违约金率", key: "prepay_rate", fmt: (r) => pct(r.prepay_rate), lower: true },
    { title: "期限", fmt: (r) => (r.term_months ? `${r.term_months} 个月` : "-") },
    { title: "还款方式", fmt: (r) => esc(r.repayment || "-") },
    { title: "担保安排", fmt: (r) => esc(r.guarantee || "-") },
  ];

  function scCells(metric, rows, multi) {
    if (!metric.key || !multi) return rows.map((r) => `<td>${metric.fmt(r)}</td>`).join("");
    const vals = rows.map((r) => Number(r[metric.key]) || 0);
    const min = Math.min(...vals), max = Math.max(...vals);
    return rows.map((r) => {
      const v = Number(r[metric.key]) || 0;
      if (max > min && v === min) return `<td class="adv"><i class="sc-flag">优势</i>${metric.fmt(r)}</td>`;
      if (max > min && v === max) return `<td class="dis"><i class="sc-flag">劣势</i>${metric.fmt(r)}</td>`;
      return `<td>${metric.fmt(r)}</td>`;
    }).join("");
  }

  function renderCompare(rows, label, compact) {
    if (!rows || !rows.length || rows.some((r) => r.hidden)) return '<div class="result-block"><div class="summary-banner"><div class="sb-body"><b>情景模拟已自动隐藏</b><p>至少一份合同缺少可确认的实际融资现金流要素，无法进行公平比较。</p></div></div></div>';
    const multi = rows.length > 1;
    const head = `<thead><tr><th class="sc-corner">对比项</th>${rows.map((r) => {
      const m = r.name.match(/^(.+?)[（(](.+?)[）)]?$/);
      const main = m ? m[1] : r.name;
      const sub = m ? m[2] : "";
      return `<th class="sc-con${r.rank === 1 ? " sc-best" : ""}"><div class="sc-head-main">${esc(main)}</div>${sub ? `<div class="sc-head-sub">${esc(sub)}</div>` : ""}${r.rank === 1 ? "<em>最优</em>" : ""}</th>`;
    }).join("")}</tr></thead>`;
    const body = `<tbody>${SC_METRICS.map((m) => `<tr><th class="sc-item${m.strong ? " sc-strong" : ""}">${m.title}</th>${scCells(m, rows, multi)}</tr>`).join("")}</tbody>`;
    return `<section class="result-block"><div class="result-title">情景对比<span class="rt-tag">${esc(label)}</span></div><table class="data-table sc-compare">${head}${body}</table>${compact ? "" : scConclusion(rows, label, multi) + (multi ? CGCharts.bars(rows.map((r) => ({ label: r.name, value: r.total_cost, display: money(r.total_cost), color: r.rank === 1 ? "#4ADE80" : "linear-gradient(90deg,#4F7CFF,#38BDF8)" })), { label: "合同情景成本比较" }) : "")}</section>`;
  }

  function scConclusion(rows, label, multi) {
    const best = rows[0];
    if (!multi) return `<div class="summary-banner gold"><div class="sb-icon">✦</div><div class="sb-body"><b>情景结论 · ${esc(label)}</b><p>${esc(best.name)} 在该情景下总融资成本 ${money(best.total_cost)}（利息 ${money(best.interest)}、固定费用 ${money(best.fees || 0)}、违约金 ${money(best.penalty || 0)}）。${esc(best.detail)}。</p></div></div>`;
    const second = rows[1];
    const gap = second.diff_vs_best ?? (second.total_cost - best.total_cost);
    const gapPct = best.total_cost ? Math.abs(gap / best.total_cost * 100).toFixed(1) : "0.0";
    const iGap = (second.interest || 0) - (best.interest || 0);
    const fGap = ((second.fees || 0) + (second.penalty || 0)) - ((best.fees || 0) + (best.penalty || 0));
    const drivers = [];
    if (Math.abs(iGap) > 0.5) drivers.push(`利息${iGap > 0 ? "多支出" : "少支出"} ${money(Math.abs(iGap))}`);
    if (Math.abs(fGap) > 0.5) drivers.push(`费用与违约金${fGap > 0 ? "多支出" : "少支出"} ${money(Math.abs(fGap))}`);
    const rateNote = (best.annual_rate != null && second.annual_rate != null && best.annual_rate !== second.annual_rate) ? `名义利率 ${pct(best.annual_rate)} 低于 ${esc(second.name)} 的 ${pct(second.annual_rate)}` : `名义利率 ${pct(best.annual_rate)} 与对手持平`;
    const prepayNote = (+best.prepay_rate || +second.prepay_rate) ? `提前还款违约金率 ${pct(best.prepay_rate)}（${esc(second.name)} 为 ${pct(second.prepay_rate)}）` : "";
    return `<div class="summary-banner gold"><div class="sb-icon">✦</div><div class="sb-body"><b>情景结论 · ${esc(label)}</b><p>${esc(best.name)} 以总成本 ${money(best.total_cost)} 排名第一，较 ${esc(second.name)} 节省 ${money(gap)}（约 ${gapPct}%）${drivers.length ? `，差距主要来自${drivers.join("、")}` : ""}。</p><p><b>综合评价：</b>该情景假设下 ${esc(best.name)} 成本结构最优，${rateNote}${prepayNote ? `，${prepayNote}` : ""}；${esc(second.name)} 虽${(second.annual_rate ?? 0) > (best.annual_rate ?? 0) ? "利率更高" : "费用更重"}，但在其他情景下可能反超，建议结合还款计划与现金流预测做最终决策，并在签约前重点复核费用与违约金条款原文。</p></div></div>`;
  }

  function renderFlip(data) {
    const h = data.hold[0], p = data.prepay[0];
    const flipNote = data.flips.length ? data.flips.map((x) => esc(x.note)).join("；") : "所选合同在两种情景下排序一致，未出现排名翻转。";
    const same = h.id === p.id;
    const conclusion = same
      ? `两种情景下最优合同一致：${esc(h.name)}（持有到期 ${money(h.total_cost)}，第 ${data.prepay_month} 个月提前还款 ${money(p.total_cost)}）。该合同在不同资金安排下均保持成本优势，签约决策确定性最高，建议优先签约并同步登记契约监控阈值。`
      : `持有到期情景最优为 ${esc(h.name)}（总成本 ${money(h.total_cost)}）；第 ${data.prepay_month} 个月提前还款情景最优为 ${esc(p.name)}（总成本 ${money(p.total_cost)}）。若企业现金流稳定、计划持有到期，建议选择 ${esc(h.name)}；若存在提前还款安排，${esc(p.name)} 的成本结构更稳健。建议结合未来 ${data.prepay_month} 个月资金计划与现金流预测做最终决策，签约前重点核对提前还款违约金条款原文。`;
    const bars = [...data.hold.map((r) => ({ label: `${r.name} · 持有`, value: r.total_cost, display: money(r.total_cost), color: r.rank === 1 ? "#4ADE80" : "linear-gradient(90deg,#4F7CFF,#38BDF8)" })), ...data.prepay.map((r) => ({ label: `${r.name} · 提前`, value: r.total_cost, display: money(r.total_cost), color: r.rank === 1 ? "#4ADE80" : "linear-gradient(90deg,#4F7CFF,#38BDF8)" }))];
    return `<div class="summary-banner gold"><div class="sb-icon">✦</div><div class="sb-body"><b>两情景综合结论</b><p>${conclusion}</p></div></div><div class="flip-note"><b>排名翻转检测：</b>${flipNote}</div><div class="dc-cols">${renderCompare(data.hold, "持有到期", true)}${renderCompare(data.prepay, `第${data.prepay_month}个月提前还款`, true)}</div>${CGCharts.bars(bars, { label: "两情景总成本对比" })}`;
  }

  const demoFinancials = [
    ["2025Q3", 128000000, 196000000, 102000000, 88000000], ["2025Q4", 133000000, 198000000, 105000000, 90000000],
    ["2026Q1", 136000000, 200000000, 108000000, 92000000], ["2026Q2", 142000000, 200000000, 106000000, 95000000],
  ];
  function addFinRow(values = ["新季度", 0, 0, 0, 0]) {
    const tr = document.createElement("tr");
    const rowValues = Array.from({ length: 6 }, (_, i) => values[i] ?? "");
    const ph = ["如 2026Q1", "如 120000000", "如 200000000", "如 108000000", "如 92000000", "如 106000000"];
    tr.innerHTML = rowValues.map((v, i) => `<td><input ${i ? 'type="number" min="0" step="10000"' : 'type="text"'} value="${esc(v)}" placeholder="${ph[i]}" aria-label="${['季度','总负债','总资产','流动资产','流动负债','速动资产'][i]}"></td>`).join("") + '<td><button class="del-btn" type="button">移除</button></td>';
    $("#fin-tbody").appendChild(tr);
  }
  function clearFinancials() { $("#fin-tbody").innerHTML = ""; }
  function loadDemoFinancials() { clearFinancials(); demoFinancials.forEach(addFinRow); toast("已载入明确标识的演示财务数据，请勿与真实数据混用", "ok"); }

  function csvCells(line, delimiter) {
    const cells = []; let cell = "", quoted = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"' && line[i + 1] === '"' && quoted) { cell += '"'; i++; }
      else if (ch === '"') quoted = !quoted;
      else if (ch === delimiter && !quoted) { cells.push(cell.trim()); cell = ""; }
      else cell += ch;
    }
    cells.push(cell.trim()); return cells;
  }

  async function importFinancialFile(file) {
    if (!file || file.size > 5 * 1024 * 1024) return toast("请选择 5MB 以内的 CSV、JSON 或 XLSX 财务数据文件", "err");
    const cid = [...state.selected.covenant][0];
    if (!cid) return toast("请先选择一份含财务契约的合同", "err");
    try {
      const data = await API.uploadFinancials(cid, file);
      $("#fin-tbody").innerHTML = "";
      (data.financials || []).forEach((x) => addFinRow([x.label, x.total_liabilities ?? "", x.total_assets ?? "", x.current_assets ?? "", x.current_liabilities ?? "", x.quick_assets ?? ""]));
      if ((data.results || []).length) $("#covenant-results").innerHTML = renderMonitor(data);
      toast(`已导入并保存 ${data.financials.length} 期财务数据，契约状态已自动重算`, "ok");
    } catch (e) { toast(`财务数据导入失败：${e.message}`, "err"); }
    finally { $("#fin-file-input").value = ""; }
  }
  function readFinancials() {
    return $$("#fin-tbody tr").map((tr) => { const v = $$("input", tr).map((i) => i.value); const num = (x) => x === "" || x == null ? null : Number(x); return { label: v[0], total_liabilities: num(v[1]), total_assets: num(v[2]), current_assets: num(v[3]), current_liabilities: num(v[4]), quick_assets: num(v[5]) }; }).filter((x) => x.label && x.total_assets > 0);
  }

  async function runCovenant() {
    const ids = [...state.selected.covenant], fins = readFinancials(), button = $("#btn-covenant-run"), root = $("#covenant-results");
    if (!ids.length || !fins.length) return toast("请选择合同并填写有效财务数据", "err");
    busy(button, true, "监控中…"); loading(root, "正在将合同阈值与最新财务数据逐项核对…");
    try {
      const data = await API.covenant({ contract_ids: ids, financials: fins });
      root.innerHTML = data.monitors.map(renderMonitor).join("");
      state.aiContext = data.monitors.flatMap((m) => m.results.map((r) => `${m.name} - ${r.indicator}：当前${r.value}，阈值${r.threshold}，状态${r.status}，距离${r.distance}`)).join("\n");
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }

  function renderMonitor(m) {
    return `<section class="result-block"><div class="result-title">${esc(m.name)}<span class="rt-tag">Covenant Guard</span></div><div class="kpi-grid"><div class="kpi"><div class="k-label">已触发</div><div class="k-value danger">${m.summary.triggered}</div></div><div class="kpi"><div class="k-label">接近阈值</div><div class="k-value warn">${m.summary.near}</div></div><div class="kpi"><div class="k-label">安全</div><div class="k-value good">${m.summary.safe}</div></div></div>${m.results.map(renderCovenantCard).join("")}</section>`;
  }
  function renderCovenantCard(r) {
    const badge = r.status === "已触发" ? "badge-red" : r.status === "接近阈值" ? "badge-amber" : r.status === "安全" ? "badge-green" : "badge-gray";
    const unit = r.indicator === "资产负债率" ? "%" : "";
    const points = r.series.filter((x) => x.value != null).map((x) => ({ label: x.label, value: x.value, display: x.value, color: x.status === "已触发" ? "#F87171" : x.status === "接近阈值" ? "#FBBF24" : "#4ADE80" }));
    return `<article class="cov-card"><div class="cov-head"><div class="cov-name">${esc(r.indicator)}<span class="badge ${badge}">${esc(r.status)}</span></div><div class="cov-thr">合同阈值：${r.direction === 'max' ? '≤' : '≥'} ${r.threshold}${unit} · ${esc(r.frequency || '')}检查</div></div><div class="cov-status-row"><div class="cov-value">${r.value == null ? '-' : r.value + unit}</div><div class="cov-dist">阈值距离：${r.distance == null ? '-' : r.distance + unit}${r.remedy_days ? ` · 补救期 ${r.remedy_days} 日` : ''}</div></div>${CGCharts.line(points, { threshold: r.threshold, unit, label: `${r.indicator}变化趋势` })}<div class="cov-advice"><b>处置建议：</b>${esc(r.advice)}</div><div class="cov-src">原文证据：<mark>${esc(r.clause || '')} ${esc(r.source || '')}</mark></div></article>`;
  }

  async function runDiff() {
    const button = $("#btn-diff-run"), root = $("#diff-results");
    if (!state.selected.old || !state.selected.newer || state.selected.old === state.selected.newer) return toast("请选择两份不同的合同版本", "err");
    busy(button, true, "对齐中…"); loading(root, "正在对齐语义相同条款并量化风险变化…");
    try {
      const data = await API.diff({ old_id: state.selected.old, new_id: state.selected.newer, remaining_principal: +$("#diff-principal").value || null });
      root.innerHTML = renderDiff(data);
      state.aiContext = `旧版：${data.old.name}\n新版：${data.new.name}\n风险上升${data.up_count}项，下降${data.down_count}项，需复核${data.review_count}项\n金额影响：${data.total_impact_text}\n结论：${data.conclusion}`;
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }
  function renderDiff(d) {
    if (d.comparable === false) return `<section class="result-block"><div class="result-title">${esc(d.old.name)} → ${esc(d.new.name)}<span class="rt-tag">Contract Diff</span></div><div class="summary-banner"><div class="sb-icon">ⓘ</div><div class="sb-body"><b>暂不比较</b><p>${esc(d.blocked_reason || d.conclusion || "两份文件无法确认属于同一合同版本")}</p></div></div></section>`;
    return `<section class="result-block"><div class="result-title">${esc(d.old.name)} → ${esc(d.new.name)}<span class="rt-tag">Contract Diff</span></div><div class="kpi-grid"><div class="kpi"><div class="k-label">风险上升</div><div class="k-value danger">${d.up_count}</div></div><div class="kpi"><div class="k-label">风险下降</div><div class="k-value good">${d.down_count}</div></div><div class="kpi"><div class="k-label">需人工复核</div><div class="k-value warn">${d.review_count}</div></div><div class="kpi"><div class="k-label">可量化成本变化</div><div class="k-value danger">${esc(d.total_impact_text)}</div></div></div><div class="summary-banner red"><div class="sb-icon">⚠</div><div class="sb-body"><b>版本风险结论</b><p>${esc(d.conclusion)}</p></div></div>${(d.changes || []).map((c) => `<article class="diff-change risk-${esc(c.risk)}"><div class="dc-head"><span class="badge ${c.risk === 'up' ? 'badge-red' : c.risk === 'down' ? 'badge-green' : 'badge-gray'}">${c.risk === 'up' ? '风险上升' : c.risk === 'down' ? '风险下降' : '人工复核'}</span><div class="dc-title">${esc(c.title)}${c.aspect ? ` · ${esc(c.aspect)}` : ''}</div>${c.impact_text ? `<div class="dc-impact ${c.risk}">${c.impact > 0 ? '+' : ''}${esc(c.impact_text)}</div>` : ''}</div><div class="dc-cols"><div class="dc-cell old"><span class="dc-tag">原条款</span>${esc(c.old_text || '无')}</div><div class="dc-cell new"><span class="dc-tag">新条款</span>${esc(c.new_text || '无')}</div></div><div class="dc-reason"><b>影响判断</b><span>${esc(c.reason)}</span></div></article>`).join("") || '<div class="summary-banner"><div class="sb-body"><b>未发现实质性变化</b></div></div>'}</section>`;
  }

  async function runOverview() {
    const ids = [...state.selected.overview], button = $("#btn-overview-run"), root = $("#overview-results");
    if (ids.length < 2) return toast("一键综合评估至少需要选择两份合同", "err");
    busy(button, true, "评估中…"); loading(root, "正在同时运行四引擎综合分析…");
    try {
      const data = await API.comprehensive({ contract_ids: ids, prepay_month: +$("#param-month")?.value || 12 });
      root.innerHTML = renderOverview(data, ids);
      state.lastOverview = { data, ids };
      state.aiContext = `综合评价：${data.overall.conclusion}\n风险等级：${data.overall.risk_level}（${data.overall.risk_score}/100）\n性价比评分：${data.overall.value_score}/100\n`;
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }

  function renderOverview(d, ids) {
    const o = d.overall;
    const riskClass = o.risk_level === "高风险" ? "danger" : o.risk_level === "中风险" ? "warn" : "good";
    const costRows = (d.costs || []).map((c) => {
      const r = c.result || {};
      if (r.hidden) return `<tr><td>${esc(c.name)}</td><td colspan="4">${esc(r.reason)}</td></tr>`;
      return `<tr><td>${esc(c.name)}</td><td class="num">${pct(r.annual_rate)}</td><td class="num">${rawPct(r.comprehensive_rate_pct)}</td><td class="num">${rawPct(r.real_annual_cost_pct)}</td><td class="num">${money(r.total_cost)}</td></tr>`;
    }).join("");
    const bestCost = (d.costs || []).filter((c) => !(c.result || {}).error && !(c.result || {}).hidden).sort((a, b) => (a.result.real_annual_cost_pct || 1e9) - (b.result.real_annual_cost_pct || 1e9))[0];
    const flipText = (d.scenario && d.scenario.flips && d.scenario.flips.length) ? `出现排名翻转：${d.scenario.flips.map((x) => esc(x.note)).join("；")}` : "两种情景下排序稳定，无排名翻转。";
    const covSummary = (d.covenants || []).map((c) => `<span>${esc(c.name)}：安全 ${c.summary.safe} · 接近阈值 ${c.summary.near} · 已触发 ${c.summary.triggered}</span>`).join("");
    const reviewList = (o.review || []).map((r) => `<li>${esc(r)}</li>`).join("");
    const knowledgeCards = (d.knowledge || []).slice(0, 3).map((k) => `<div class="know-card"><b>${esc(k.title)}</b><p>${esc(k.text)}</p><a href="${esc(k.url)}" target="_blank" rel="noopener">来源：${esc(k.source)}</a></div>`).join("");
    // 报告导出工具栏：屏幕上显示，打印时由 print.css 隐藏
    const toolbar = `<div class="report-toolbar"><div class="rt-note"><b>报告已生成</b><span>${ids.length} 份合同 · 四引擎联动 · 结论可回溯原文</span></div><div class="rt-actions"><button class="btn btn-ghost btn-sm" id="btn-copy-link" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>复制报告链接</button><button class="btn btn-ghost btn-sm" id="btn-print-report" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9V3h12v6M6 18H4v-7h16v7h-2M7 14h10v7H7z" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>导出打印版（A4 / PDF）</button></div></div>`;
    return toolbar + `<section class="result-block"><div class="result-title">一键综合评估报告<span class="rt-tag">四引擎联动</span></div><div class="overview-top"><div class="ov-grade"><div class="ov-label">综合风险等级</div><div class="ov-value ${riskClass}">${esc(o.risk_level)}</div><div class="ov-sub">评分 ${o.risk_score}/100（越低越安全）· ${esc(o.method)}</div></div><div class="ov-grade"><div class="ov-label">性价比评估</div><div class="ov-value">${o.value_score}<small>/100</small></div><div class="ov-sub">分数越高代表融资成本与风险越优</div></div></div><div class="summary-banner ${riskClass === 'danger' ? 'red' : riskClass === 'warn' ? 'gold' : 'green'}"><div class="sb-icon">◆</div><div class="sb-body"><b>综合评价结论</b><p>${esc(o.conclusion)}</p></div></div><div class="ov-section"><div class="ov-sec-title">一、融资成本对比（Cost Lens）</div><table class="data-table"><thead><tr><th>合同</th><th>名义利率</th><th>综合成本率</th><th>真实年化成本</th><th>总融资成本</th></tr></thead><tbody>${costRows}</tbody></table>${bestCost ? `<div class="ov-tip"><b>成本优势合同：</b>${esc(bestCost.name)}，真实年化成本 ${rawPct(bestCost.result.real_annual_cost_pct)}</div>` : ""}</div><div class="ov-section"><div class="ov-sec-title">二、情景比较结论（Scenario Compare）</div><div class="ov-pair"><div><b>持有到期最优</b><span>${esc(d.scenario.hold[0].name)} · 总成本 ${money(d.scenario.hold[0].total_cost)}</span></div><div><b>提前还款最优</b><span>第 ${d.scenario.prepay_month} 个月 · ${esc(d.scenario.prepay[0].name)} · 总成本 ${money(d.scenario.prepay[0].total_cost)}</span></div></div><div class="ov-tip">${esc(flipText)}</div></div><div class="ov-section"><div class="ov-sec-title">三、契约监控（Covenant Guard）</div><div class="ov-cov">${covSummary || "所选合同未识别到财务契约条款。"}</div></div><div class="ov-section"><div class="ov-sec-title">四、版本风险（Contract Diff）</div><div class="ov-diff"><span>风险上升 <b class="danger">${d.diff.up_count}</b> 项</span><span>风险下降 <b class="good">${d.diff.down_count}</b> 项</span><span>需复核 <b class="warn">${d.diff.review_count}</b> 项</span><span>金额影响 <b class="danger">${esc(d.diff.total_impact_text)}</b></span></div></div><div class="ov-section"><div class="ov-sec-title">五、待复核事项</div><ul class="ov-review">${reviewList}</ul></div>${knowledgeCards ? `<div class="ov-section"><div class="ov-sec-title">六、金融知识与合规提示</div><div class="know-grid">${knowledgeCards}</div></div>` : ""}</section>`;
  }

  /* ══ C1：综合评估报告导出打印版 ══
     走浏览器原生打印（与 Cmd/Ctrl+P 同一通道），不依赖服务端 PDF 组件：
     离线可用、零额外依赖、图表以矢量输出。打印版式全部由 css/print.css 提供。 */
  function reportStamp() {
    const d = new Date(), p = (n) => String(n).padStart(2, "0");
    const ymd = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`, hm = `${p(d.getHours())}${p(d.getMinutes())}`;
    return {
      ymd, hm,
      readable: `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`,
      no: `CG-${ymd}-${hm}`,
    };
  }

  // 填充打印专用页眉页脚（这些元素在屏幕上 display:none）。
  // 挂在 beforeprint 上，因此「点导出按钮」和「直接 Cmd/Ctrl+P」两条路都能拿到完整页眉。
  function fillPrintHeader() {
    const ov = state.lastOverview;
    if (!ov) return;
    const stamp = reportStamp();
    const ids = ov.ids || [...state.selected.overview];
    const names = ids.map((id) => (state.contracts.find((c) => c.id === id) || {}).name || id);
    const shown = names.slice(0, 3).join("、") + (names.length > 3 ? ` 等 ${names.length} 份` : "");
    const o = ov.data.overall || {};
    if ($("#ph-time")) $("#ph-time").textContent = `生成时间　${stamp.readable}`;
    if ($("#ph-subjects")) $("#ph-subjects").textContent = `评估对象：${shown}`;
    if ($("#ph-meta")) $("#ph-meta").textContent =
      `报告编号：${stamp.no}　·　综合风险等级：${o.risk_level || "—"}${o.risk_score != null ? `（${o.risk_score}/100）` : ""}　·　性价比评分：${o.value_score != null ? `${o.value_score}/100` : "—"}`;
    if ($("#ph-no-foot")) $("#ph-no-foot").textContent = `ContractGuard 综合评估报告 · ${stamp.no} · ${stamp.readable}`;
  }

  function printReport() {
    if (!state.lastOverview) return toast("请先运行一次综合评估，再导出打印版", "err");
    fillPrintHeader();
    const stamp = reportStamp();

    // 借 document.title 控制「另存为 PDF」的默认文件名；打印结束后还原
    const prevTitle = document.title;
    document.title = `ContractGuard综合评估报告_${stamp.ymd}_${stamp.hm}`;
    let restored = false;
    const restore = () => {
      if (restored) return;
      restored = true;
      document.title = prevTitle;
      window.removeEventListener("afterprint", restore);
    };
    window.addEventListener("afterprint", restore);
    setTimeout(() => window.print(), 80);
    setTimeout(restore, 120000); // 兜底：个别浏览器不派发 afterprint
  }

  /* C2：复制「打开即自动出报告」的链接。
     127.0.0.1 属安全上下文可用 Clipboard API；通过局域网 http 访问时会降级为弹窗手动复制。 */
  async function copyReportLink() {
    const url = shareUrl("overview");
    try {
      await navigator.clipboard.writeText(url);
      toast("链接已复制：队友打开后会自动生成同一份报告", "ok");
    } catch (e) {
      window.prompt("复制下面的链接，发给队友可直接打开这份报告：", url);
    }
  }

  async function runAgentTask() {
    const task = $("#agent-task-input").value.trim(), ids = [...state.selected.agent], root = $("#agent-results"), button = $("#btn-agent-run");
    if (!task) return toast("请选择或输入一个任务", "err");
    if (!ids.length) return toast("请至少选择一份合同", "err");
    busy(button, true, "Agent 处理中…"); loading(root, "正在判断合同类型、字段完整度与最合适的分析工具…");
    try {
      const data = await API.agentTask({ task, contract_ids: ids, financials: task.includes("财务") || task.includes("契约") ? readFinancials() : null });
      root.innerHTML = renderAgentTask(data); state.aiContext = data.ai_context || `任务：${task}\n工具：${data.tool_trace.map(x=>x.tool+':'+x.status).join('；')}`;
    } catch (e) { root.innerHTML = ""; toast(e.message, "err"); }
    finally { busy(button, false); }
  }

  function renderAgentTask(data) {
    const trace = data.tool_trace.map((x) => `<div class="agent-trace ${x.status}"><span>${x.status === 'called' ? '已调用' : x.status === 'available' ? '可用' : '已跳过'}</span><b>${esc(x.tool)}</b><p>${esc(x.reason)}</p><em>${x.result_type === 'ai_judgment' ? 'AI 判断' : '确定性代码'}</em></div>`).join("");
    const reviews = data.human_review || [];
    const resultKeys = Object.keys(data.results || {});
    const findings = data.ai_summary || [];
    const findingsBlock = findings.length
      ? `<div class="ov-section"><div class="ov-sec-title">关键发现（${findings.length} 项）</div><ul class="ov-review">${findings.map((x) => `<li><b>${esc(x.title)}</b>（${esc(x.level || '未分级')}）<br>${esc(x.why || '')}<br>依据：${esc(x.evidence || '—')}${x.clause ? '（' + esc(x.clause) + '）' : ''}<br>建议：${esc(x.advice || '—')}</li>`).join('')}</ul></div>`
      : "";
    return `<section class="result-block"><div class="result-title">任务执行报告<span class="rt-tag">${esc(data.task)}</span></div><div class="agent-scope"><b>外部模型</b><span>${data.external_llm.used ? `已启用 · 范围：${esc(data.external_llm.scope)}` : '未调用'}</span><b>关键计算</b><span>全部由确定性代码完成</span></div><div class="agent-trace-list">${trace}</div>${findingsBlock}<div class="summary-banner gold"><div class="sb-body"><b>执行结果</b><p>已生成 ${resultKeys.length} 类结果：${esc(resultKeys.join('、') || '无可执行结果')}。${reviews.length ? `有 ${reviews.length} 项进入人工复核。` : '当前无新增人工复核项。'}</p></div></div>${reviews.length ? `<div class="ov-section"><div class="ov-sec-title">人工复核问题</div><ul class="ov-review">${reviews.map((x)=>`<li>${esc(x.label)}：${esc(x.text)}</li>`).join('')}</ul></div>` : ''}<details class="agent-raw"><summary>查看结构化任务结果</summary><pre>${esc(JSON.stringify(data.results, null, 2))}</pre></details></section>`;
  }

  async function persistRename(id, input, onDone) {
    const value = input.value.trim();
    if (!value) return toast("名称不能为空", "err");
    if (value.length > 80) return toast("名称不能超过 80 个字符", "err");
    try {
      await API.renameContract(id, value);
      await loadContracts();
      if (onDone) await onDone();
      toast("合同名称已保存", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  function beginLibraryRename(button) {
    const id = button.dataset.rename;
    const row = button.closest(".lib-name-row");
    const current = state.contracts.find((c) => c.id === id)?.name || "";
    if (!row || row.querySelector(".rename-editor")) return;
    row.innerHTML = `<input class="rename-editor" maxlength="80" value="${esc(current)}"><span class="rename-actions"><button type="button" data-rename-save>保存</button><button type="button" data-rename-cancel>取消</button></span>`;
    const input = row.querySelector(".rename-editor");
    input.focus(); input.select();
    row.querySelector("[data-rename-save]").onclick = () => persistRename(id, input);
    row.querySelector("[data-rename-cancel]").onclick = () => renderLibrary();
    input.onkeydown = (e) => { if (e.key === "Enter") persistRename(id, input); if (e.key === "Escape") renderLibrary(); };
  }

  function beginDetailRename(button) {
    const id = button.dataset.renameDetail, title = $(".cd-title h3", $("#contract-detail"));
    const current = state.contracts.find((c) => c.id === id)?.name || title?.textContent || "";
    if (!title || title.querySelector(".rename-editor")) return;
    title.innerHTML = `<input class="rename-editor rename-editor-detail" maxlength="80" value="${esc(current)}"><span class="rename-actions"><button type="button" data-rename-save>保存</button><button type="button" data-rename-cancel>取消</button></span>`;
    const input = title.querySelector(".rename-editor"); input.focus(); input.select();
    title.querySelector("[data-rename-save]").onclick = () => persistRename(id, input, () => showContract(id));
    title.querySelector("[data-rename-cancel]").onclick = () => showContract(id);
    input.onkeydown = (e) => { if (e.key === "Enter") persistRename(id, input, () => showContract(id)); if (e.key === "Escape") showContract(id); };
    button.disabled = true;
  }

  function openFieldEditor(cid, field) {
    $("#field-edit-cid").value = cid; $("#field-edit-key").value = field.key;
    $("#field-edit-label").textContent = field.label; $("#field-edit-value").value = field.value ?? "";
    $("#field-edit-display").value = field.display || ""; $("#field-edit-evidence").textContent = field.source_text || "原文未定位；修正不会创建虚构证据。";
    $("#field-edit-modal").style.display = "flex";
  }

  async function saveFieldEdit() {
    const cid=$("#field-edit-cid").value,key=$("#field-edit-key").value, raw=$("#field-edit-value").value.trim(), display=$("#field-edit-display").value.trim();
    const resolution=$("#field-resolution button.active")?.dataset.r || "adopt_value";
    let value=raw; if (["amount","rate","term","actual_financing_amount","credit_limit","credit_term"].includes(key) && raw!=="") value=Number(raw);
    try { busy($("#field-edit-save"),true,"重新计算中…"); await API.updateField(cid,key,{value,display,source:"user_edit",resolution}); $("#field-edit-modal").style.display="none"; await loadContracts(); await showContract(cid); toast("字段已确认，相关成本与风险结果已重新计算","ok"); }
    catch(e){toast(e.message,"err");} finally{busy($("#field-edit-save"),false);}
  }

  async function refreshSecurity() {
    try { const s=await API.security(); $("#deterministic-only").checked=s.deterministic_only; $("#security-state").textContent=s.deterministic_only?"仅本地规则和确定性引擎 · 不向外部模型发送数据":`本地规则计算 · 外部模型范围：${s.external_data}`; } catch(_){}
  }

  async function refreshAIStatus() {
    try {
      const s = await API.aiStatus();
      $$("#ai-dot,#ai-dot2").forEach((x) => x.classList.toggle("on", s.configured));
      $("#ai-status-text").textContent = s.configured ? `${s.label} 已连接` : "AI 内置引擎";
      $("#ai-engine-label").textContent = s.configured ? `${s.label} · ${s.model}` : "内置确定性引擎";
      $("#ai-model-input").value = s.model || "";
      $("#ai-proxy-input").value = s.proxy || "";
      $$("#provider-seg button").forEach((b) => b.classList.toggle("active", b.dataset.p === s.provider));
    } catch (_) { $("#ai-status-text").textContent = "AI 状态未知"; }
  }

  async function refreshRuntimeStatus() {
    const box = $("#service-health"), warning = $("#demo-warning");
    try {
      const h = await API.health();
      window.__contractguardShareMode = Boolean(h.share_mode);
      box.classList.toggle("ok", Boolean(h.ok));
      box.classList.toggle("err", !h.ok);
      const mode = h.share_mode ? "公网共享" : (h.demo_mode ? "本地演示" : "本地开发");
      box.querySelector("span").textContent = `${mode} · 服务正常 · 数据已持久化`;
      warning.hidden = !h.demo_mode;
    } catch (_) {
      box.classList.add("err");
      box.querySelector("span").textContent = "服务状态异常";
      warning.hidden = false;
    }
  }

  function addAIMessage(role, text = "") {
    const row = document.createElement("div"); row.className = `ai-msg ${role}`;
    row.innerHTML = `<div class="ai-avatar">${role === 'bot' ? 'AI' : '你'}</div><div class="ai-bubble"></div>`;
    $(".ai-bubble", row).textContent = text; $("#ai-messages").appendChild(row); $("#ai-messages").scrollTop = $("#ai-messages").scrollHeight;
    return $(".ai-bubble", row);
  }
  async function sendAI(forced) {
    const input = $("#ai-input"), q = (forced || input.value).trim(); if (!q) return;
    input.value = ""; addAIMessage("user", q); state.aiMessages.push({ role: "user", content: q });
    const bubble = addAIMessage("bot", ""); bubble.innerHTML = '<span class="cursor"></span>'; busy($("#ai-send"), true, "生成中");
    let answer = "";
    try {
      await aiChatStream(state.aiMessages.slice(-8), state.aiContext || null, (chunk) => { answer += chunk; bubble.textContent = answer; bubble.insertAdjacentHTML("beforeend", '<span class="cursor"></span>'); $("#ai-messages").scrollTop = $("#ai-messages").scrollHeight; }, null);
      bubble.textContent = answer || "未收到模型回复，请稍后重试。"; state.aiMessages.push({ role: "assistant", content: answer });
    } catch (e) { bubble.textContent = `AI 连接异常：${e.message}。确定性分析引擎仍可正常使用。`; }
    finally { busy($("#ai-send"), false); }
  }

  async function demoTour() {
    if (state.demoRunning) return;
    const overlay = $("#demo-overlay"), selectSec = $("#demo-select"), runSec = $("#demo-run");
    const bar = $("#demo-progress-bar"), stageTitle = $("#demo-stage-title"), caseLabel = $("#demo-case-label");
    const live = $("#demo-live"), skipBtn = $("#demo-skip"), closeBtn = $("#demo-close");
    const engines = $$("#demo-run [data-demo-step]");
    const cases = {
      finance: { label: "企业融资方案决策", ids: ["SC002", "SC003"] },
      renewal: { label: "融资租赁续签审查", ids: ["SC005", "SC006"] },
    };
    const stageNames = ["导入合同", "AI 条款解析", "Cost Lens 成本分析", "Scenario Compare 情景比较", "Covenant Guard 契约监控", "Contract Diff 版本比对", "综合评价生成"];
    const liveTexts = [
      "正在读取合同原文，解析 PDF / DOCX 结构…",
      "识别金额、利率、费用、担保与契约条款…",
      "重构现金流，计算综合成本率与真实年化成本…",
      "在持有到期与提前还款情景下比较两份合同…",
      "将财务红线转为监控规则，核对契约阈值…",
      "对齐新旧版本条款，定位数字与责任变化…",
      "汇总四引擎结果，输出风险等级与性价比评分…",
    ];
    const wait = (ms) => new Promise((r) => {
      const t = setTimeout(r, ms);
      const c = setInterval(() => { if (state.demoSkipped) { clearTimeout(t); clearInterval(c); r(); } }, 80);
    });
    function closeOverlay() { state.demoRunning = false; overlay.style.display = "none"; }

    overlay.style.display = "flex";
    selectSec.style.display = ""; runSec.style.display = "none";
    state.demoSkipped = false;

    const picked = await new Promise((resolve) => {
      $$(".demo-case", selectSec).forEach((b) => b.onclick = () => resolve(b.dataset.case));
      closeBtn.onclick = () => resolve(null);
    });
    if (!picked) { closeOverlay(); return; }

    const c = cases[picked] || cases.finance;
    state.demoRunning = true;
    caseLabel.textContent = c.label;
    selectSec.style.display = "none"; runSec.style.display = "";
    engines.forEach((el) => el.classList.remove("active", "done"));
    bar.style.width = "0%";
    skipBtn.onclick = () => { state.demoSkipped = true; };
    closeBtn.onclick = () => { state.demoSkipped = true; };

    for (let i = 0; i < engines.length; i++) {
      if (state.demoSkipped) break;
      engines.forEach((el, j) => { el.classList.toggle("active", j === i); el.classList.toggle("done", j < i); });
      stageTitle.textContent = stageNames[i] || stageNames[stageNames.length - 1];
      live.textContent = liveTexts[i] || liveTexts[liveTexts.length - 1];
      bar.style.width = ((i + 1) / engines.length * 100).toFixed(1) + "%";
      await wait(1500);
    }
    if (!state.demoSkipped) {
      engines.forEach((el) => { el.classList.add("done"); el.classList.remove("active"); });
      stageTitle.textContent = "分析完成";
      live.textContent = "正在打开一键综合评估报告…";
      bar.style.width = "100%";
      await wait(700);
    }

    closeOverlay();
    state.selected.overview = new Set(c.ids);
    renderPickers();
    showView("overview");
    runOverview();
  }

  function bind() {
    $("#nav").onclick = (e) => { const b = e.target.closest("[data-view]"); if (b) showView(b.dataset.view); };
    $$("[data-goto]").forEach((b) => b.onclick = () => showView(b.dataset.goto));
    $(".brand").onclick = () => showView("home");
    $("#btn-replay-intro").onclick = () => window.__replayIntro ? window.__replayIntro() : location.reload();
    $("#btn-agent-run").onclick = runAgentTask; $("#task-templates").onclick = (e) => { const b=e.target.closest("[data-task]"); if(b) $("#agent-task-input").value=b.dataset.task; };
    $("#deterministic-only").onchange = async () => { await API.updateSecurity({deterministic_only:$("#deterministic-only").checked,external_scope:"masked_evidence_only"}); await refreshSecurity(); toast("数据处理策略已更新","ok"); };
    $("#btn-costlens-run").onclick = runCost; $("#btn-scenario-run").onclick = runScenario; $("#btn-covenant-run").onclick = runCovenant; $("#btn-diff-run").onclick = runDiff; $("#btn-overview-run").onclick = runOverview;
    // 委托绑定：结果区每次重渲染都会重建按钮，绑在容器上才不会失效
    $("#overview-results").onclick = (e) => {
      if (e.target.closest("#btn-print-report")) printReport();
      else if (e.target.closest("#btn-copy-link")) copyReportLink();
    };
    // 直接按 Cmd/Ctrl+P 时也补全报告页眉
    window.addEventListener("beforeprint", fillPrintHeader);
    $("#scenario-tabs").onclick = (e) => { const b = e.target.closest("button[data-s]"); if (!b) return; state.scenario = b.dataset.s; $$("button", $("#scenario-tabs")).forEach((x) => x.classList.toggle("active", x === b)); $("#param-prepay").style.display = state.scenario === "prepay" ? "flex" : "none"; $("#param-rate").style.display = state.scenario === "rate_up" ? "flex" : "none"; $("#param-stress").style.display = ["stress", "overdue"].includes(state.scenario) ? "flex" : "none"; };
    [["#param-month", "#param-month-val", 0], ["#param-delta", "#param-delta-val", 1], ["#param-late", "#param-late-val", 0]].forEach(([a, b, d]) => $(a).oninput = () => $(b).textContent = Number($(a).value).toFixed(d));
    $("#btn-fin-import").onclick = () => $("#fin-file-input").click(); $("#fin-file-input").onchange = () => importFinancialFile($("#fin-file-input").files[0]);
    $("#btn-fin-add").onclick = () => addFinRow(); $("#btn-fin-reset").onclick = loadDemoFinancials; $("#fin-tbody").onclick = (e) => { if (e.target.closest(".del-btn")) e.target.closest("tr").remove(); };
    const zone = $("#upload-zone"), file = $("#file-input"); zone.onclick = (e) => { if (!e.target.closest("button")) file.click(); };
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0])); file.onchange = () => file.files[0] && upload(file.files[0]);
    $("#btn-paste-text").onclick = (e) => { e.stopPropagation(); $("#paste-modal").style.display = "flex"; }; $("#paste-cancel").onclick = () => $("#paste-modal").style.display = "none";
    $("#paste-submit").onclick = async () => { const name = $("#paste-name").value.trim() || "粘贴合同", text = $("#paste-text").value; try { busy($("#paste-submit"), true, "解析中…"); await API.importText(name, text); $("#paste-modal").style.display = "none"; $("#paste-text").value = ""; await loadContracts(); toast("合同已解析并加入合同库", "ok"); } catch (e) { toast(e.message, "err"); } finally { busy($("#paste-submit"), false); } };
    $("#lib-list").onclick = async (e) => {
      const rename = e.target.closest("[data-rename]");
      if (rename) { e.stopPropagation(); beginLibraryRename(rename); return; }
      const del = e.target.closest("[data-delete]");
      if (del) { e.stopPropagation(); try { await API._fetch(`/api/contracts/${del.dataset.delete}`, { method: "DELETE" }); await loadContracts(); $("#contract-detail").innerHTML = ""; toast("合同已删除", "ok"); } catch (err) { toast(err.message, "err"); } return; }
      const item = e.target.closest(".lib-item"); if (item) showContract(item.dataset.id);
    };
    $("#contract-detail").onclick = async (e) => {
      if (e.target.closest("[data-close-detail]")) return $("#contract-detail").innerHTML = "";
      const rename = e.target.closest("[data-rename-detail]"); if (rename) { e.stopPropagation(); beginDetailRename(rename); return; }
      const riskEvidence = e.target.closest(".risk-evidence"); if (riskEvidence) { e.stopPropagation(); bindEvidence(riskEvidence); return; }
      const edit=e.target.closest("[data-edit-field]"); if(edit){e.stopPropagation(); const f=state.currentDetail?.contract?.fields?.[edit.dataset.editField]; if(f) openFieldEditor(state.currentContract,f); return;}
      if(e.target.closest("[data-reparse]")){try{busy(e.target.closest("button"),true,"解析中…");await API._fetch(`/api/contracts/${state.currentContract}/reparse`,{method:"POST"});await showContract(state.currentContract);toast("已重新解析并保留用户修正","ok");}catch(err){toast(err.message,"err");}return;}
      const rb=e.target.closest("[data-review-status]"); if(rb){const li=rb.closest("[data-review-id]");await API.updateReview(state.currentContract,li.dataset.reviewId,{status:rb.dataset.reviewStatus,note:""});li.classList.add("review-done");toast("复核状态已保存","ok");}
    };
    $("#field-edit-cancel").onclick=()=>$("#field-edit-modal").style.display="none"; $("#field-edit-save").onclick=saveFieldEdit;
    $("#field-resolution").onclick=(e)=>{const b=e.target.closest("[data-r]");if(b)$$("button",$("#field-resolution")).forEach(x=>x.classList.toggle("active",x===b));};
    $("#ai-fab").onclick = () => $("#ai-panel").classList.toggle("open"); $("#ai-close").onclick = () => $("#ai-panel").classList.remove("open"); $("#ai-send").onclick = () => sendAI(); $("#ai-input").onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); sendAI(); } }; $("#ai-quick").onclick = (e) => { const b = e.target.closest("button[data-q]"); if (b) sendAI(b.dataset.q); };
    $("#btn-ai-settings").onclick = () => $("#ai-modal").style.display = "flex"; $("#ai-modal-close").onclick = () => $("#ai-modal").style.display = "none"; $("#provider-seg").onclick = (e) => { const b = e.target.closest("button[data-p]"); if (!b) return; $$("button", $("#provider-seg")).forEach((x) => x.classList.toggle("active", x === b)); };
    $("#ai-save-btn").onclick = () => saveAI(false); $("#ai-test-btn").onclick = () => saveAI(true);
  }

  async function upload(file) {
    const allowed = [".docx", ".pdf", ".txt", ".md"], ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!allowed.includes(ext) || file.size > 15 * 1024 * 1024) return toast("请上传 15MB 以内的 docx / pdf / txt 文件", "err");
    const title = $(".uz-title"); title.textContent = `正在解析 ${file.name}…`;
    try { const data = await API.uploadContract(file); await loadContracts(); title.textContent = "拖拽合同文件到此处，或点击选择"; toast(`已提取 ${data.contract.clause_count} 个条款，可立即分析`, "ok"); showContract(data.contract.id); } catch (e) { title.textContent = "拖拽合同文件到此处，或点击选择"; toast(e.message, "err"); }
  }

  async function saveAI(test) {
    const provider = $("#provider-seg button.active")?.dataset.p || "openai", model = $("#ai-model-input").value.trim(), proxy = $("#ai-proxy-input").value.trim(), button = test ? $("#ai-test-btn") : $("#ai-save-btn"), result = $("#ai-test-result");
    busy(button, true, test ? "测试中…" : "保存中…");
    try { const data = await API.aiConfig({ provider, model: model || undefined, proxy, test }); await refreshAIStatus(); if (test) { result.className = `test-result ${data.test?.ok ? 'ok' : 'err'}`; result.textContent = data.test?.message || "未配置后端 API Key"; } else { $("#ai-modal").style.display = "none"; toast("AI 服务商设置已更新；密钥仍仅由后端环境变量读取", "ok"); } } catch (e) { result.className = "test-result err"; result.textContent = e.message; } finally { busy(button, false); }
  }

  async function init() {
    bind(); clearFinancials();
    try { await Promise.all([loadContracts(), refreshAIStatus(), refreshSecurity(), refreshRuntimeStatus()]); } catch (e) { toast(`初始化失败：${e.message}`, "err"); }
    $("#view-container").scrollTop = 0;
    // C2：先按地址栏还原视图与勾选；没有 hash 时补写一个，保证地址栏随时可复制分享
    if (!applyFromHash()) syncUrl(state.view, false);
    window.addEventListener("hashchange", applyFromHash);
    window.addEventListener("popstate", applyFromHash);
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
