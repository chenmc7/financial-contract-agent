/* ContractGuard 轻量数据可视化：不依赖第三方图表库 */
(function () {
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

  function line(series, options = {}) {
    const values = (series || []).map((d) => Number(d.value)).filter(Number.isFinite);
    if (!values.length) return '<div class="hint-text">暂无可绘制数据</div>';
    const width = 760, height = 210, left = 48, right = 24, top = 22, bottom = 38;
    const min = options.min != null ? Number(options.min) : Math.min(...values);
    const max0 = options.max != null ? Number(options.max) : Math.max(...values);
    const pad = Math.max((max0 - min) * 0.16, Math.abs(max0 || 1) * 0.04);
    const lo = options.min != null ? min : min - pad;
    const hi = options.max != null ? max0 : max0 + pad;
    const x = (i) => left + (series.length === 1 ? 0 : i * (width - left - right) / (series.length - 1));
    const y = (v) => top + (hi - Number(v)) * (height - top - bottom) / Math.max(hi - lo, 1e-9);
    const points = series.map((d, i) => `${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(" ");
    const threshold = Number.isFinite(Number(options.threshold))
      ? `<line x1="${left}" y1="${y(options.threshold)}" x2="${width - right}" y2="${y(options.threshold)}" stroke="#FBBF24" stroke-width="1.5" stroke-dasharray="6 6"/><text x="${width - right}" y="${y(options.threshold) - 7}" fill="#FBBF24" font-size="11" text-anchor="end">阈值 ${esc(options.threshold)}${esc(options.unit || "")}</text>` : "";
    const dots = series.map((d, i) => `<g><circle cx="${x(i)}" cy="${y(d.value)}" r="5" fill="#0E1729" stroke="${d.color || '#38BDF8'}" stroke-width="3"/><text x="${x(i)}" y="${height - 12}" fill="#71809F" font-size="11" text-anchor="middle">${esc(d.label)}</text><text x="${x(i)}" y="${y(d.value) - 12}" fill="#E7ECF7" font-size="11" font-weight="700" text-anchor="middle">${esc(d.display ?? d.value)}${esc(options.unit || "")}</text></g>`).join("");
    return `<div class="chart-box"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.label || '趋势图')}"><line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="rgba(148,163,184,.18)"/>${threshold}<polyline points="${points}" fill="none" stroke="url(#cgLine)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><defs><linearGradient id="cgLine" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#4F7CFF"/><stop offset="1" stop-color="#38BDF8"/></linearGradient></defs>${dots}</svg></div>`;
  }

  function bars(items, options = {}) {
    const values = (items || []).map((d) => Math.max(0, Number(d.value) || 0));
    const max = Math.max(...values, 1);
    return `<div class="cg-bars" role="img" aria-label="${esc(options.label || '对比图')}">${items.map((d, i) => `<div class="cg-bar-row"><span>${esc(d.label)}</span><i><b style="width:${(values[i] / max * 100).toFixed(2)}%;background:${d.color || 'linear-gradient(90deg,#4F7CFF,#38BDF8)'}"></b></i><strong>${esc(d.display ?? d.value)}</strong></div>`).join("")}</div>`;
  }

  window.CGCharts = { line, bars };
})();
