/* ContractGuard API 客户端 */
const API = {
  async _fetch(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      let msg = "请求失败 (" + res.status + ")";
      try {
        const j = await res.json();
        msg = j.detail || msg;
      } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return res.json();
  },
  listContracts: () => API._fetch("/api/contracts"),
  getContract: (id) => API._fetch("/api/contracts/" + id),
  renameContract: (id, displayName) => API._fetch(`/api/contracts/${id}/name`, {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName }),
  }),
  uploadContract: async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/contracts/upload", { method: "POST", body: fd });
    if (!res.ok) {
      let msg = "上传失败 (" + res.status + ")";
      try { const j = await res.json(); msg = j.detail || msg; } catch (e) { }
      throw new Error(msg);
    }
    return res.json();
  },
  importText: (name, text) =>
    API._fetch("/api/contracts/import-text", {
      method: "POST",
      body: JSON.stringify({ name, text }),
    }),
  costlens: (ids, transactionIds = {}) =>
    API._fetch("/api/analyze/costlens", {
      method: "POST",
      body: JSON.stringify({ contract_ids: ids, transaction_ids: transactionIds }),
    }),
  scenario: (body) =>
    API._fetch("/api/analyze/scenario", { method: "POST", body: JSON.stringify(body) }),
  covenant: (body) =>
    API._fetch("/api/analyze/covenant", { method: "POST", body: JSON.stringify(body) }),
  diff: (body) =>
    API._fetch("/api/analyze/diff", { method: "POST", body: JSON.stringify(body) }),
  comprehensive: (body) =>
    API._fetch("/api/analyze/comprehensive", { method: "POST", body: JSON.stringify(body) }),
  agentTask: (body) => API._fetch("/api/agent/tasks", { method: "POST", body: JSON.stringify(body) }),
  updateField: (cid, key, body) => API._fetch(`/api/contracts/${cid}/fields/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
  updateReview: (cid, item, body) => API._fetch(`/api/contracts/${cid}/reviews/${encodeURIComponent(item)}`, { method: "POST", body: JSON.stringify(body) }),
  security: () => API._fetch("/api/security"),
  updateSecurity: (body) => API._fetch("/api/security", { method: "POST", body: JSON.stringify(body) }),
  uploadFinancials: async (cid, file) => {
    const fd = new FormData(); fd.append("file", file);
    const res = await fetch(`/api/contracts/${cid}/financials/upload`, { method: "POST", body: fd });
    if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || "财务数据上传失败"); }
    return res.json();
  },
  aiStatus: () => API._fetch("/api/ai/status"),
  health: () => API._fetch("/api/health"),
  aiConfig: (body) =>
    API._fetch("/api/ai/config", { method: "POST", body: JSON.stringify(body) }),
};

/* AI 流式对话（SSE 解析） */
async function aiChatStream(messages, context, onDelta, onMeta) {
  if (window.__contractguardShareMode) {
    const data = await API._fetch("/api/ai/chat-once", {
      method: "POST",
      body: JSON.stringify({ messages, context }),
    });
    onMeta && onMeta({ type: "meta", engine: data.engine });
    onDelta && onDelta(data.content || "");
    return;
  }
  const res = await fetch("/api/ai/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, context }),
  });
  if (!res.ok) throw new Error("AI 接口调用失败 (" + res.status + ")");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n\n");
    buf = lines.pop();
    for (const line of lines) {
      const data = line.replace(/^data:\s*/, "").trim();
      if (!data) continue;
      try {
        const obj = JSON.parse(data);
        if (obj.type === "meta") onMeta && onMeta(obj);
        else if (obj.type === "delta") onDelta && onDelta(obj.content);
        else if (obj.type === "error") throw new Error(obj.message);
      } catch (e) {
        if (e instanceof SyntaxError) continue;
        throw e;
      }
    }
  }
}
