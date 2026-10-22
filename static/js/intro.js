/* ═══════════════════════════════════════════
   开场动画时间轴控制器
   总时长 19.2s < 20s（含 0.7s 退出过渡）
   场景：品牌 3.4s → 条款 4.0s → 引擎 5.0s → 环 3.2s → 定版 3.6s
   ═══════════════════════════════════════════ */
(function () {
  const intro = document.getElementById("intro");
  if (!intro) return;

  const SCENES = [
    { el: null, dur: 3400 },   // 1 品牌揭示
    { el: null, dur: 4000 },   // 2 条款提取
    { el: null, dur: 5000 },   // 3 四维引擎
    { el: null, dur: 3200 },   // 4 生命周期环
    { el: null, dur: 3600 },   // 5 定版
  ];
  document.querySelectorAll(".intro-scene").forEach((s) => {
    const idx = parseInt(s.dataset.scene, 10) - 1;
    if (SCENES[idx]) SCENES[idx].el = s;
  });

  const TOTAL = SCENES.reduce((a, s) => a + s.dur, 0); // 19200ms
  const progressBar = document.getElementById("intro-progress-bar");
  const skipBtn = document.getElementById("intro-skip");
  const enterBtn = document.querySelector(".s5-enter");

  let sceneIdx = -1;
  let sceneTimer = null;
  let exitTimer = null;
  let progressRAF = null;
  let startTime = 0;
  let done = false;

  function showScene(i) {
    SCENES.forEach((s, j) => s.el.classList.toggle("active", j === i));
    sceneIdx = i;
  }

  function nextScene() {
    if (done) return;
    const next = sceneIdx + 1;
    if (next < SCENES.length) {
      showScene(next);
      sceneTimer = setTimeout(nextScene, SCENES[next].dur);
    } else {
      exitIntro(); // 全部场景播完，自动进入应用
    }
  }

  function startProgress() {
    startTime = performance.now();
    const tick = (now) => {
      if (done) return;
      const pct = Math.min((now - startTime) / TOTAL, 1) * 100;
      progressBar.style.width = pct.toFixed(2) + "%";
      progressRAF = requestAnimationFrame(tick);
    };
    progressRAF = requestAnimationFrame(tick);
  }

  function exitIntro() {
    if (done) return;
    done = true;
    clearTimeout(sceneTimer);
    clearTimeout(exitTimer);
    if (progressRAF) cancelAnimationFrame(progressRAF);
    intro.classList.add("out");
    document.getElementById("app").classList.remove("app-hidden");
    setTimeout(() => { intro.remove(); }, 750);
    window.dispatchEvent(new CustomEvent("intro:done"));
  }

  // 启动
  window.addEventListener("load", () => {
    startProgress();
    showScene(0);
    sceneTimer = setTimeout(nextScene, SCENES[0].dur);
  });

  skipBtn.addEventListener("click", exitIntro);
  if (enterBtn) enterBtn.addEventListener("click", exitIntro);

  // 对外暴露重播能力
  window.__replayIntro = function () {
    // 重建 intro DOM（已删除时需要重新插入）
    if (!document.getElementById("intro")) {
      location.reload();
      return;
    }
    done = false;
    sceneIdx = -1;
    startTime = performance.now();
    intro.classList.remove("out");
    document.getElementById("app").classList.add("app-hidden");
    startProgress();
    showScene(0);
    sceneTimer = setTimeout(nextScene, SCENES[0].dur);
  };

  // 键盘：Esc 跳过
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !done) exitIntro();
  });
})();
