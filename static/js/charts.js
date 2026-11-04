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

  /* ── 逐期现金流：全量期数不抽样、三序列（还款/本金/利息）+ 期末余额右轴 ──
     期数多时按最小像素宽度撑开画布并横向滚动，保证每期都有真实数据点。 */

  const FLOW_SERIES = [
    { key: "total", label: "每期还款", color: "#4F7CFF", w: 2.6 },
    { key: "principal", label: "其中本金", color: "#4ADE80", w: 1.8 },
    { key: "interest", label: "其中利息", color: "#FBBF24", w: 1.8 },
  ];

  const trimZero = (s) => (s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s);

  function shortMoney(v) {
    const n = Number(v) || 0;
    const a = Math.abs(n);
    if (a >= 1e8) return trimZero((n / 1e8).toFixed(2)) + "亿";
    if (a >= 1e4) return trimZero((n / 1e4).toFixed(a >= 1e6 ? 0 : 1)) + "万";
    return trimZero(n.toFixed(0));
  }

  const fmtYuan = (v) => (Number(v) || 0).toFixed(2) + " 元";

  function niceStep(raw) {
    const x = Math.max(Number(raw) || 0, 1e-6);
    const e = Math.pow(10, Math.floor(Math.log10(x)));
    const f = x / e;
    const m = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return m * e;
  }

  function flow(rows, options = {}) {
    const pts = (rows || []).filter((d) => d && Number.isFinite(Number(d.period)));
    if (pts.length < 1) return '<div class="hint-text">暂无可绘制的逐期还款计划</div>';

    const unit = options.unit || "期";
    const n = pts.length;
    const num = (v) => Number(v) || 0;
    const left = 64, right = 60, top = 26, bottom = 50;
    /* 常规期数（≤60）按容器宽度铺满，确保末期断崖一眼可见；
       期数极多时才撑出最小宽度并横向滚动，不裁掉任何一期 */
    const minW = Math.max(520, Math.round(n * 11) + left + right);
    const width = Math.max(860, Math.round(n * 12) + left + right);
    const height = 244;
    const plotW = width - left - right, plotH = height - top - bottom;

    const amtMax = Math.max(...pts.map((d) => Math.max(num(d.total), num(d.principal), num(d.interest))), 1);
    const balMax = Math.max(...pts.map((d) => num(d.balance)), 1);

    const aStep = niceStep(amtMax / 4);
    let aTop = Math.ceil(amtMax / aStep) * aStep;
    if (aTop < amtMax * 1.02) aTop += aStep;
    const bHalf = niceStep(balMax / 2);
    const bTop = Math.ceil(balMax / bHalf) * bHalf;

    const x = (i) => left + (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));
    const yA = (v) => top + (aTop - num(v)) * plotH / aTop;
    const yB = (v) => top + (bTop - num(v)) * plotH / Math.max(bTop, 1e-9);

    /* 左侧金额轴：按整齐步长铺网格 */
    let grid = "";
    for (let k = 0; k * aStep <= aTop + 1e-6; k++) {
      const v = k * aStep, yy = yA(v).toFixed(1);
      grid += `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" stroke="rgba(148,163,184,.14)"/>`
        + `<text x="${left - 8}" y="${(yA(v) + 4).toFixed(1)}" fill="#71809F" font-size="10.5" text-anchor="end">${esc(shortMoney(v))}</text>`;
    }
    /* 右侧余额轴：只标 0 / 中 / 顶，避免与金额轴打架 */
    [0, bTop / 2, bTop].forEach((v) => {
      grid += `<text x="${width - right + 8}" y="${(yB(v) + 4).toFixed(1)}" fill="#5C6A85" font-size="10" text-anchor="start">${esc(v === 0 ? "0" : shortMoney(v))}</text>`;
    });

    /* 先画细分项、最后画总还款，避免总额线被同值序列盖住 */
    const polylines = FLOW_SERIES.slice().reverse().map((s) => {
      const d = pts.map((p, i) => `${x(i).toFixed(1)},${yA(p[s.key]).toFixed(1)}`).join(" ");
      return `<polyline class="cg-series" points="${d}" fill="none" stroke="${s.color}" stroke-width="${s.w}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
    }).join("");
    const balanceLine = `<polyline class="cg-series cg-balance" points="${pts.map((p, i) => `${x(i).toFixed(1)},${yB(p.balance).toFixed(1)}`).join(" ")}" fill="none" stroke="#94A3B8" stroke-width="1.4" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;

    /* 期数少时才画点，多时靠悬停热区取精确值 */
    const dots = n <= 18
      ? pts.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${yA(p.total).toFixed(1)}" r="3.2" fill="#0E1729" stroke="${FLOW_SERIES[0].color}" stroke-width="2"/>`).join("")
      : "";

    const colW = n === 1 ? plotW : plotW / Math.max(n - 1, 1);
    const zones = pts.map((p, i) => `<rect x="${(x(i) - colW / 2).toFixed(1)}" y="${top}" width="${colW.toFixed(1)}" height="${plotH}" fill="transparent"><title>第${esc(p.period)}${esc(unit)}｜还款 ${esc(fmtYuan(p.total))}（本金 ${esc(fmtYuan(p.principal))}／利息 ${esc(fmtYuan(p.interest))}）｜期末余额 ${esc(fmtYuan(p.balance))}</title></rect>`).join("");

    /* X 轴刻度：等距取样，保留首尾 */
    const step = Math.max(1, Math.ceil(n / 9));
    const idxs = [];
    for (let i = 0; i < n; i += step) idxs.push(i);
    if (idxs[idxs.length - 1] !== n - 1) {
      if (n - 1 - idxs[idxs.length - 1] < step * 0.6) idxs.pop();
      idxs.push(n - 1);
    }
    const ticks = idxs.map((i) => `<text x="${x(i).toFixed(1)}" y="${height - bottom + 20}" fill="#71809F" font-size="10.5" text-anchor="middle">${esc(pts[i].period)}</text>`).join("");

    const totalSum = pts.reduce((a, p) => a + num(p.total), 0);
    const lastShare = totalSum > 0 ? num(pts[n - 1].total) / totalSum : 0;
    const peak = pts.reduce((a, b) => (num(b.total) > num(a.total) ? b : a), pts[0]);
    const advice = n > 1 && lastShare >= 0.3
      ? `末期一次性支出占全周期 <b>${(lastShare * 100).toFixed(1)}%</b>，还款高度集中，需提前预留到期资金。`
      : `共 <b>${n}</b> ${esc(unit)}，单期最高支出 <b>${esc(shortMoney(num(peak.total)))} 元</b>（第 ${esc(peak.period)} ${esc(unit)}）。`;

    const legend = FLOW_SERIES.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")
      + '<span><i class="dash"></i>期末余额（右轴）</span>';

    const scrollTitle = n > 65 ? ' title="期数较多，图表可左右滑动查看全部期数"' : "";
    return `<div class="cg-flow" role="img" aria-label="${esc(options.label || "逐期还款现金流结构")}">`
      + `<div class="cg-flow-head"><div class="cg-flow-legend">${legend}</div><span class="cg-flow-unit">单位：元</span></div>`
      + `<div class="cg-flow-scroll"${scrollTitle}><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" style="width:100%;min-width:${minW}px">${grid}${polylines}${balanceLine}${dots}${zones}${ticks}</svg></div>`
      + `<p class="cg-flow-note">${advice}</p></div>`;
  }

  window.CGCharts = { line, bars, flow };
})();
