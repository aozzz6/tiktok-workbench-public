/* ============================================================
   fx.js · 零依赖「效果层」：把 react-bits 的四种效果用原生重写
   用法：app.html?s=S016&fx=aurora,spotlight,spark,magnet,text
   不开 fx 时对页面零影响；不引 React、不加体积（本文件 ~6KB）
   ============================================================ */
window.OVFX = (function () {
  const root = document.documentElement;

  /* 1. 极光背景：三层彩色光斑 + CSS 漂移（对应 react-bits 的 Aurora） */
  function aurora() {
    const box = document.createElement('div');
    box.className = 'fx-aurora';
    box.innerHTML = '<i class="b1"></i><i class="b2"></i><i class="b3"></i><i class="b4"></i>';
    document.body.prepend(box);
  }

  /* 2. 聚光跟随：给 .k / .mod 挂 --mx/--my，CSS 用径向渐变画光斑（对应 SpotlightCard） */
  function spotlight() {
    root.classList.add('fx-spot');
    const mark = (el, e) => {
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
      el.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
    };
    document.addEventListener('pointermove', (e) => {
      const el = e.target.closest('.k, .mod, .u-find, .acc-card');
      if (el) mark(el, e);
    }, { passive: true });
  }

  /* 3. 点击火花：canvas 粒子（对应 ClickSpark） */
  function spark(opts) {
    const c = document.createElement('canvas');
    c.className = 'fx-spark';
    document.body.appendChild(c);
    const ctx = c.getContext('2d');
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let parts = [], raf = 0;
    const resize = () => {
      c.width = innerWidth * dpr; c.height = innerHeight * dpr;
      c.style.width = innerWidth + 'px'; c.style.height = innerHeight + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    addEventListener('resize', resize);
    const color = () => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#5ef2b8';
    const tick = () => {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      parts = parts.filter((p) => p.life > 0);
      parts.forEach((p) => {
        p.vx *= 0.97; p.vy = p.vy * 0.97 + 0.16; p.x += p.vx; p.y += p.vy; p.life -= 0.018;
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.strokeStyle = p.c; ctx.lineWidth = 1.6; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - p.vx * 2.2, p.y - p.vy * 2.2); ctx.stroke();
      });
      ctx.globalAlpha = 1;
      raf = parts.length ? requestAnimationFrame(tick) : 0;
    };
    document.addEventListener('pointerdown', (e) => {
      const c1 = color();
      const n = opts.count || 9;
      for (let i = 0; i < n; i++) {
        const a = (Math.PI * 2 * i) / n + Math.random() * 0.5;
        const s = 2.4 + Math.random() * 3.2;
        parts.push({ x: e.clientX, y: e.clientY, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 1.2, life: 1, c: c1 });
      }
      if (!raf) raf = requestAnimationFrame(tick);
    }, { passive: true });
  }

  /* 4. 磁吸按钮：指针靠近时按钮轻微偏移（对应 Magnet） */
  function magnet() {
    const SEL = '.btn, [data-act], .a6-item, .kb-card';
    const items = new Map();
    const reset = (el) => el.style.transform = '';
    addEventListener('pointermove', (e) => {
      document.querySelectorAll(SEL).forEach((el) => {
        const r = el.getBoundingClientRect();
        const dx = e.clientX - (r.left + r.width / 2);
        const dy = e.clientY - (r.top + r.height / 2);
        const dist = Math.hypot(dx, dy);
        const reach = Math.max(r.width, r.height) * 0.9;
        if (dist < reach) {
          const k = (1 - dist / reach) * 6;
          el.style.transform = 'translate(' + (dx / dist * k).toFixed(1) + 'px,' + (dy / dist * k).toFixed(1) + 'px)';
          items.set(el, true);
        } else if (items.has(el)) { reset(el); items.delete(el); }
      });
    }, { passive: true });
  }

  /* 5. 流光标题：给 h1 上渐变裁切 + 位移动画（对应 GradientText，慎用，会盖掉语义色） */
  function text() { root.classList.add('fx-text'); }

  function init(list) {
    const set = new Set(list || []);
    if (set.has('aurora')) aurora();
    if (set.has('spotlight')) spotlight();
    if (set.has('spark')) spark({});
    if (set.has('magnet')) magnet();
    if (set.has('text')) text();
    root.dataset.fx = [...set].join(' ');
  }

  return { init };
})();
