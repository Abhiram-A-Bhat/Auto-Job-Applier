/* Liquid Glass behaviour layer. Plain JS (no build step) so both server-rendered apps can use it.
   - Refraction: per-size displacement maps (rounded-rect lens, strongest at the bezel) fed to an SVG
     feDisplacementMap with RGB split (chromatic aberration), applied as backdrop-filter. Chromium only;
     other browsers keep frosted glass.
   - Light: rims face the light source (pointer on desktop, device tilt on phones); specular hotspot follows the pointer.
   - Motion: scroll reveals, split-text headings, card tilt, magnetic buttons, ripple + jelly press, particle bursts,
     animated <details>, scroll progress, velocity skew, parallax aurora, cursor glow + ring, count-up, analog gauge. */
(() => {
  "use strict";
  const doc = document, root = doc.documentElement, body = doc.body;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const chromium = brands.some(b => /Chromium/i.test(b.brand));
  const NS = "http://www.w3.org/2000/svg";
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const mk = (tag, cls, parent) => {
    const e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  };

  // ---------- world ----------
  const world = mk("div", "lg-world");
  world.setAttribute("aria-hidden", "true");
  for (let i = 1; i <= 6; i++) mk("div", "blob b" + i, world);
  body.prepend(world);
  mk("div", "lg-grain", body).setAttribute("aria-hidden", "true");
  const progress = mk("div", "lg-progress", body);
  let glow = null, ring = null;
  if (fine && !reduce) {
    glow = mk("div", "lg-cursor", body);
    ring = mk("div", "lg-ring", body);
  }
  const defs = doc.createElementNS(NS, "svg");
  defs.setAttribute("aria-hidden", "true");
  defs.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;pointer-events:none";
  defs.innerHTML = '<defs><radialGradient id="lg-cap" cx="35%" cy="30%"><stop offset="0" stop-color="#fff"/>' +
    '<stop offset=".5" stop-color="#c9ced6"/><stop offset="1" stop-color="#6b7280"/></radialGradient></defs>';
  body.appendChild(defs);

  // ---------- refraction ----------
  const cache = new Map();
  let fid = 0;
  const sdRect = (px, py, hx, hy, r) => {
    const qx = Math.abs(px) - hx + r, qy = Math.abs(py) - hy + r;
    return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - r;
  };
  function mapURL(w, h, r, bezel) {
    const k = w * h > 160000 ? 0.5 : 1;
    const W = Math.max(2, Math.round(w * k)), H = Math.max(2, Math.round(h * k)), R = r * k, B = Math.max(1, bezel * k);
    const c = mk("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d"), img = ctx.createImageData(W, H), d = img.data, hx = W / 2, hy = H / 2;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const px = x + 0.5 - hx, py = y + 0.5 - hy, s = sdRect(px, py, hx, hy, R);
        let dx = 0, dy = 0;
        if (s < 0 && -s < B) {
          const gx = sdRect(px + 1, py, hx, hy, R) - sdRect(px - 1, py, hx, hy, R);
          const gy = sdRect(px, py + 1, hx, hy, R) - sdRect(px, py - 1, hx, hy, R);
          const len = Math.hypot(gx, gy) || 1, t = 1 + s / B, m = Math.pow(t, 2.2); // convex bezel: bends most at the rim
          dx = (-gx / len) * m; dy = (-gy / len) * m;
        }
        const i = (y * W + x) * 4;
        d[i] = 128 + dx * 127; d[i + 1] = 128 + dy * 127; d[i + 2] = 128; d[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    return c.toDataURL();
  }
  const MAT = ["1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0", "0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0",
    "0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0"];
  function filterFor(w, h, r) {
    w = Math.max(8, Math.round(w / 4) * 4);
    h = Math.max(8, Math.round(h / 4) * 4);
    r = Math.round(Math.min(r, w / 2, h / 2));
    const key = w + "x" + h + "x" + r;
    if (cache.has(key)) return cache.get(key);
    const id = "lgf" + (fid++), bezel = Math.min(26, Math.min(w, h) * 0.32), scale = clamp(Math.min(w, h) * 0.5, 14, 56);
    const f = doc.createElementNS(NS, "filter");
    const at = { id, x: 0, y: 0, width: w, height: h, filterUnits: "userSpaceOnUse", primitiveUnits: "userSpaceOnUse",
      "color-interpolation-filters": "sRGB" };
    for (const k in at) f.setAttribute(k, at[k]);
    let s = `<feImage href="${mapURL(w, h, r, bezel)}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="none" result="map"/>`;
    ["R", "G", "B"].forEach((ch, i) => {
      s += `<feDisplacementMap in="SourceGraphic" in2="map" scale="${(scale * (1 - i * 0.07)).toFixed(1)}" ` +
        `xChannelSelector="R" yChannelSelector="G" result="d${ch}"/>` +
        `<feColorMatrix in="d${ch}" type="matrix" values="${MAT[i]}" result="c${ch}"/>`;
    });
    s += '<feBlend in="cR" in2="cG" mode="screen" result="rg"/><feBlend in="rg" in2="cB" mode="screen"/>';
    f.innerHTML = s;
    defs.appendChild(f);
    cache.set(key, id);
    return id;
  }
  function refract(el) {
    if (!chromium || reduce || !el.isConnected) return;
    const w = el.offsetWidth, h = el.offsetHeight;
    if (w < 36 || h < 24) return;
    const r = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
    const v = `url(#${filterFor(w, h, r)}) blur(${el.classList.contains("lg-btn") ? 2 : 7}px) saturate(1.8) brightness(1.05)`;
    el.style.setProperty("backdrop-filter", v);
    el.style.setProperty("-webkit-backdrop-filter", v);
  }
  const ro = "ResizeObserver" in window ? new ResizeObserver(es => es.forEach(e => refract(e.target))) : null;

  // ---------- reveal ----------
  let booted = false;
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("lg-in"); io.unobserve(e.target); }
  }), { threshold: 0.06, rootMargin: "0px 0px -5% 0px" });
  function reveal(el, i) {
    if (el.classList.contains("lg-reveal")) return;
    if (reduce || (booted && el.closest("[data-live]"))) { el.classList.add("lg-in"); return; }
    el.classList.add("lg-reveal");
    el.style.setProperty("--i", i % 10);
    io.observe(el);
  }
  function split(h) {
    if (h.dataset.split || h.children.length || !h.textContent.trim()) return;
    h.dataset.split = "1";
    const t = h.textContent.trim();
    h.setAttribute("aria-label", t);
    h.textContent = "";
    const wrap = mk("span", "lg-split", h);
    wrap.setAttribute("aria-hidden", "true");
    [...t].forEach((ch, i) => {
      const s = mk("span", "", wrap);
      s.textContent = ch === " " ? " " : ch;
      s.style.setProperty("--i", i);
    });
  }

  // ---------- cards ----------
  function card(el) {
    if (el.dataset.lg) return;
    el.dataset.lg = "card";
    el.classList.add("lg-card");
    refract(el);
    if (ro) ro.observe(el);
    if (!fine || reduce || el.matches("nav")) return;
    el.addEventListener("pointermove", e => {
      if (e.target.closest("input, textarea, select")) return;
      const r = el.getBoundingClientRect(), k = clamp(260 / r.height, 0.2, 1);
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      el.style.setProperty("--rx", ((0.5 - py) * 7 * k).toFixed(2) + "deg");
      el.style.setProperty("--ry", ((px - 0.5) * 8 * k).toFixed(2) + "deg");
      el.classList.add("is-tilting");
    });
    el.addEventListener("pointerleave", () => {
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
      el.classList.remove("is-tilting");
    });
  }

  // ---------- buttons ----------
  const mags = new Set();
  function button(b) {
    if (b.dataset.lg) return;
    b.dataset.lg = "btn";
    b.classList.add("lg-btn");
    if (!b.querySelector(".lg-label")) {
      const l = mk("span", "lg-label");
      while (b.firstChild) l.appendChild(b.firstChild);
      b.appendChild(l);
    }
    refract(b);
    if (ro) ro.observe(b);
    b.addEventListener("pointerdown", e => press(b, e));
    if (fine && !reduce) mags.add(b);
  }
  function press(b, e) {
    const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height) * 2.4, s = mk("span", "lg-ripple", b);
    s.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
    setTimeout(() => s.remove(), 800);
    if (!reduce && b.animate) {
      b.animate([{ scale: "1 1" }, { scale: "0.92 1.07", offset: 0.22 }, { scale: "1.06 0.95", offset: 0.5 },
        { scale: "0.98 1.02", offset: 0.76 }, { scale: "1 1" }], { duration: 600, easing: "ease-out" });
    }
  }
  const COLORS = ["#0a84ff", "#30d158", "#ffd60a", "#ff375f", "#bf5af2", "#64d2ff"];
  function burst(x, y, n = 20) {
    if (reduce) return;
    for (let i = 0; i < n; i++) {
      const p = mk("span", "lg-particle", body), a = (Math.PI * 2 * i) / n + Math.random() * 0.6, d = 50 + Math.random() * 80;
      const c = COLORS[i % COLORS.length];
      p.style.cssText = `left:${x}px;top:${y}px;background:${c};color:${c}`;
      p.animate([
        { transform: "translate(-50%,-50%) scale(1)", opacity: 1 },
        { transform: `translate(calc(-50% + ${Math.cos(a) * d}px), calc(-50% + ${Math.sin(a) * d - 20}px)) scale(.15)`, opacity: 0 },
      ], { duration: 650 + Math.random() * 350, easing: "cubic-bezier(.2,.8,.2,1)" }).onfinish = () => p.remove();
    }
  }
  doc.addEventListener("submit", e => {
    const f = e.target, sub = e.submitter;
    if (f.dataset.go) return;
    if (sub) sub.classList.add("is-busy");
    if (sub && sub.classList.contains("ok") && !reduce) {
      e.preventDefault();
      const r = sub.getBoundingClientRect();
      burst(r.left + r.width / 2, r.top + r.height / 2);
      f.dataset.go = "1";
      setTimeout(() => (f.requestSubmit ? f.requestSubmit(sub) : f.submit()), 340);
    }
  }, true);

  // ---------- details: animated open/close ----------
  function details(d) {
    const s = d.querySelector(":scope > summary");
    if (!s || s.dataset.lg) return;
    s.dataset.lg = "1";
    s.addEventListener("click", e => {
      if (reduce || !d.animate) return;
      e.preventDefault();
      if (d._anim) d._anim.cancel();
      const opening = !d.open, start = d.offsetHeight;
      d.open = opening;
      const end = d.offsetHeight;
      d.open = true;
      d.style.overflow = "hidden";
      d._anim = d.animate({ height: [start + "px", end + "px"] }, { duration: 460, easing: "cubic-bezier(.2,.8,.2,1)" });
      d._anim.onfinish = () => { d.open = opening; d.style.overflow = ""; d._anim = null; };
      d._anim.oncancel = () => { d.style.overflow = ""; };
      if (opening) {
        [...d.children].filter(c => c !== s).forEach((c, i) => c.animate(
          [{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }],
          { duration: 420, delay: 70 + i * 45, easing: "ease-out", fill: "backwards" }));
      }
    });
  }

  // ---------- count-up + analog gauge ----------
  function count(el) {
    const to = +el.dataset.count || 0;
    if (reduce) { el.textContent = to.toLocaleString(); return; }
    const t0 = performance.now(), dur = 1500;
    const step = t => {
      const k = Math.min(1, (t - t0) / dur);
      el.textContent = Math.round(to * (1 - Math.pow(1 - k, 4))).toLocaleString();
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function gauge(g) {
    if (g.dataset.lg) return;
    g.dataset.lg = "1";
    const v = +g.dataset.value || 0, max = +g.dataset.max || 100, A0 = -120, A1 = 120;
    const ang = val => A0 + (A1 - A0) * clamp(val / max, 0, 1);
    const pt = (a, r) => { const t = ((a - 90) * Math.PI) / 180; return [100 + r * Math.cos(t), 100 + r * Math.sin(t)]; };
    const arc = (a0, a1, r) => {
      const [x0, y0] = pt(a0, r), [x1, y1] = pt(a1, r);
      return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    };
    let s = '<svg viewBox="0 0 200 200" class="gauge-face" aria-hidden="true">' +
      `<path class="z1" d="${arc(-120, 24, 82)}"/><path class="z2" d="${arc(24, 72, 82)}"/><path class="z3" d="${arc(72, 120, 82)}"/>`;
    for (let i = 0; i <= 50; i++) {
      const a = A0 + ((A1 - A0) * i) / 50, major = i % 10 === 0, [x0, y0] = pt(a, major ? 64 : 69), [x1, y1] = pt(a, 75);
      s += `<line class="tk${major ? " M" : ""}" x1="${x0.toFixed(2)}" y1="${y0.toFixed(2)}" x2="${x1.toFixed(2)}" y2="${y1.toFixed(2)}"/>`;
      if (major) {
        const [tx, ty] = pt(a, 53);
        s += `<text class="tl" x="${tx.toFixed(1)}" y="${ty.toFixed(1)}">${Math.round((max * i) / 50 / 1000)}k</text>`;
      }
    }
    s += '<g class="needle"><path d="M100 108 L96.8 100 L100 30 L103.2 100 Z"/><circle class="cap" cx="100" cy="100" r="8"/></g></svg>';
    g.innerHTML = s + `<div class="gauge-read"><b data-count="${Math.round(v)}">0</b><span>${g.dataset.label || ""}</span></div>`;
    const needle = g.querySelector(".needle");
    needle.style.transform = `rotate(${A0}deg)`;
    const go = () => {
      needle.style.transition = "transform 1.9s cubic-bezier(.34,1.56,.64,1)";
      needle.style.transform = `rotate(${ang(v)}deg)`;
      count(g.querySelector("[data-count]"));
      setTimeout(() => needle.classList.add("idle"), 2000);
    };
    new IntersectionObserver((es, o) => {
      if (es[0].isIntersecting) { o.disconnect(); setTimeout(go, reduce ? 0 : 300); }
    }).observe(g);
  }

  // ---------- enhance (initial + anything added later, e.g. polled panels) ----------
  function each(scope, sel, fn) {
    if (scope.matches && scope.matches(sel)) fn(scope);
    if (scope.querySelectorAll) scope.querySelectorAll(sel).forEach(fn);
  }
  function enhance(scope) {
    let i = 0;
    each(scope, ".box, details, nav, .lg-glass", el => { card(el); if (!el.matches("nav")) reveal(el, i++); });
    each(scope, "button", button);
    each(scope, "details", details);
    each(scope, "h2", el => { split(el); reveal(el, 0); });
    each(scope, ".gauge", gauge);
  }
  ["panel", "queue", "usage", "main"].forEach(id => { const e = doc.getElementById(id); if (e) e.setAttribute("data-live", ""); });
  enhance(doc);
  setTimeout(() => { booted = true; }, 1500);
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
    if (n.nodeType === 1 && !n.matches(".lg-ripple, .lg-particle, .lg-label")) enhance(n);
  }))).observe(body, { childList: true, subtree: true });

  // ---------- pointer, tilt-light, scroll: one rAF loop ----------
  let mx = innerWidth / 2, my = innerHeight * 0.2, gx = mx, gy = my, rx = mx, ry = my, rs = 1, wx = 0, wy = 0;
  let hot = false, moved = true, sy = scrollY, skew = 0, lastP = -1, lastSkew = 0;
  addEventListener("pointermove", e => {
    mx = e.clientX; my = e.clientY; moved = true;
    const t = e.target instanceof Element ? e.target : null;
    hot = !!(t && t.closest("button, a, summary, input, textarea, select, label"));
    if (!t) return;
    for (const el of [t.closest(".lg-btn"), t.closest(".lg-card")]) {
      if (!el) continue;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", (((mx - r.left) / r.width) * 100).toFixed(1) + "%");
      el.style.setProperty("--my", (((my - r.top) / r.height) * 100).toFixed(1) + "%");
    }
  }, { passive: true });
  // phones: the light follows how you tilt the device (iOS needs a permission prompt, so it keeps the default light)
  if (!fine && !reduce && "DeviceOrientationEvent" in window && typeof DeviceOrientationEvent.requestPermission !== "function") {
    addEventListener("deviceorientation", e => {
      if (e.gamma == null) return;
      mx = innerWidth / 2 + (clamp(e.gamma, -40, 40) / 40) * innerWidth * 0.6;
      my = innerHeight / 2 + (clamp(e.beta - 45, -40, 40) / 40) * innerHeight * 0.6;
      moved = true;
    }, { passive: true });
  }
  const nav = doc.querySelector("nav");
  function frame() {
    if (moved) {
      root.style.setProperty("--lg-angle", (Math.atan2(my - innerHeight / 2, mx - innerWidth / 2) * 180 / Math.PI + 90).toFixed(1) + "deg");
      if (fine && !reduce) {
        for (const b of mags) {
          if (!b.isConnected) { mags.delete(b); continue; }
          const r = b.getBoundingClientRect(), dx = mx - (r.left + r.width / 2), dy = my - (r.top + r.height / 2);
          const near = Math.abs(dx) < r.width / 2 + 26 && Math.abs(dy) < r.height / 2 + 20;
          b.style.setProperty("--tx", (near ? clamp(dx * 0.22, -10, 10) : 0).toFixed(1) + "px");
          b.style.setProperty("--ty", (near ? clamp(dy * 0.3, -6, 6) : 0).toFixed(1) + "px");
        }
      }
      moved = false;
    }
    if (glow) {
      gx = lerp(gx, mx, 0.16); gy = lerp(gy, my, 0.16); rx = lerp(rx, mx, 0.3); ry = lerp(ry, my, 0.3); rs = lerp(rs, hot ? 1.75 : 1, 0.18);
      glow.style.transform = `translate3d(${gx}px,${gy}px,0)`;
      ring.style.transform = `translate3d(${rx}px,${ry}px,0) scale(${rs.toFixed(3)})`;
      ring.classList.toggle("hot", hot);
    }
    if (!reduce) {
      wx = lerp(wx, (mx / innerWidth - 0.5) * -34, 0.05);
      wy = lerp(wy, (my / innerHeight - 0.5) * -34 - scrollY * 0.07, 0.08);
      world.style.transform = `translate3d(${wx.toFixed(1)}px,${wy.toFixed(1)}px,0)`;
      const v = scrollY - sy;
      sy = scrollY;
      skew = lerp(skew, clamp(v * 0.05, -2.5, 2.5), 0.18);
      if (Math.abs(skew - lastSkew) > 0.005) { root.style.setProperty("--skew", skew.toFixed(3) + "deg"); lastSkew = skew; }
    }
    const max = root.scrollHeight - innerHeight, p = max > 0 ? scrollY / max : 0;
    if (Math.abs(p - lastP) > 0.001) { progress.style.setProperty("--p", p.toFixed(4)); lastP = p; }
    if (nav) nav.classList.toggle("scrolled", scrollY > 10);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
