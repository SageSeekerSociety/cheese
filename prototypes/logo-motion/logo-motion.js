/*!
 * 知是 logo 动效 · 咬痕 nibble（方向一，推荐）
 *
 * 把 logo.svg 内联进页面后调用：
 *   const m = logoMotion.attach(document.querySelector('#logo'));
 *   m.play();            // 开场只咬一次，之后轻轻待机下去
 * 触发方式：attach 之后默认自己播一遍（页面里通常就这一句），
 * 想手动控制就 attach(svg, { auto: false }) 再调 play()。
 * prefers-reduced-motion: reduce 时不做任何动画，直接给 logo 的原样静帧。
 *
 * 几何全部从内联的 logo.svg 里读出来（最大的那条 path 是月亮，它的 mask 里
 * 三条 black path 是胡须，mask 里再补八个圆就是八个洞），没有第二份坐标，
 * 换 logo 尺寸或改路径都不用动这个文件。
 */
(function (root) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  // 鼻尖的兜底位置（胡须的左下角），按 viewBox 比例存；正常情况下会从胡须的量出来
  var NOSE = [0.4967, 0.5567];
  // 进场：一口一口往外啃
  var T = 1050, FIRST = 90, STAGGER = 55, OPEN = 380;
  // 胡须扫开与眼睛接光
  var W0 = 560, WD = 380, G0 = 820, GD = 230;
  // 待机：啃食波纹、眼睛、光
  var CHEW = 5200, WINK = 4300, FADE = 1200;
  var BREATH_A = 6200, BREATH_B = 9100, BREATH_AMP = 0.017;
  var HOVER = 620, BITE = 900;

  var clamp01 = function (v) { return v < 0 ? 0 : v > 1 ? 1 : v; };
  var easeOutCubic = function (t) { return 1 - Math.pow(1 - t, 3); };
  var reduced = function () {
    return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };
  var round = function (v) { return String(Math.round(v * 100) / 100); };

  function attach(svg, opts) {
    opts = opts || {};
    if (!svg) return null;
    if (svg.__logoMotion) return svg.__logoMotion;

    // ---- 从内联的 logo.svg 里认几何 -----------------------------------------
    var scene = svg.querySelector('[mask^="url("]');
    if (!scene) return null;
    var maskId = (scene.getAttribute('mask').match(/#([^)\s"']+)/) || [])[1];
    var mask = maskId && svg.querySelector('mask[id="' + maskId + '"]');
    if (!mask) return null;

    var moon = null;
    var paths = [].slice.call(scene.querySelectorAll('path'));
    for (var i = 0; i < paths.length; i++) {
      if (!moon || paths[i].getAttribute('d').length > moon.getAttribute('d').length) moon = paths[i];
    }
    if (!moon) return null;
    var subs = moon.getAttribute('d').split(/(?=M)/g);
    var whiskers = [].slice.call(mask.querySelectorAll('path[fill="black"]'));
    var eye = scene.querySelector('circle');
    var vb = (svg.getAttribute('viewBox') || '0 0 1200 1200').split(/[\s,]+/).map(Number);

    // 鼻尖：胡须包围盒的左下角再往外一点（胡须是从鼻尖长出来的）
    var nose = { x: vb[0] + NOSE[0] * vb[2], y: vb[1] + NOSE[1] * vb[3] };
    var bx = 1e9, by = 1e9;
    whiskers.forEach(function (w) {
      var bb = w.getBBox();
      bx = Math.min(bx, bb.x);
      by = Math.min(by, bb.y);
    });
    if (bx < 1e9) nose = { x: bx - vb[2] * 0.0248, y: by + vb[3] * 0.068 };

    // 量每个洞：把子路径塞进一条临时 path 里量包围盒（洞是正圆，包围盒就是它）
    var probe = document.createElementNS(NS, 'path');
    probe.setAttribute('fill', 'none');
    moon.parentNode.insertBefore(probe, moon);
    var holes = [];
    for (var j = 1; j < subs.length; j++) {
      probe.setAttribute('d', subs[j]);
      var b = probe.getBBox();
      holes.push({ cx: b.x + b.width / 2, cy: b.y + b.height / 2, r: b.width / 2 });
    }
    probe.parentNode.removeChild(probe);
    holes.forEach(function (h) { h.d = Math.hypot(h.cx - nose.x, h.cy - nose.y); });
    holes.sort(function (a, b2) { return a.d - b2.d; }); // 离鼻尖近的先被啃

    // ---- 画出来：外轮廓那条 path + mask 里的八个圆 --------------------------
    var cut = document.createElementNS(NS, 'path');
    cut.setAttribute('class', moon.getAttribute('class') || '');
    cut.setAttribute('fill', moon.getAttribute('fill') || '');
    cut.setAttribute('d', subs[0]);
    cut.style.display = 'none';
    moon.parentNode.insertBefore(cut, moon);

    var cuts = holes.map(function (h) {
      var c = document.createElementNS(NS, 'circle');
      c.setAttribute('cx', h.cx);
      c.setAttribute('cy', h.cy);
      c.setAttribute('r', 0);
      c.setAttribute('fill', '#000');
      mask.appendChild(c);
      return c;
    });

    // 点击时那一口
    var biteCircle = document.createElementNS(NS, 'circle');
    biteCircle.setAttribute('cx', vb[0] + vb[2] * 0.583);
    biteCircle.setAttribute('cy', vb[1] + vb[3] * 0.463);
    biteCircle.setAttribute('r', 0);
    biteCircle.setAttribute('fill', '#000');
    mask.appendChild(biteCircle);

    var glint = document.createElementNS(NS, 'circle');
    glint.setAttribute('cx', (eye && eye.getAttribute('cx')) || nose.x);
    glint.setAttribute('cy', (eye && eye.getAttribute('cy')) || nose.y);
    glint.setAttribute('r', (eye && eye.getAttribute('r')) || 15);
    glint.setAttribute('fill', '#FFF0B8');
    glint.setAttribute('opacity', 0);
    if (eye) eye.parentNode.insertBefore(glint, eye.nextSibling);
    else scene.appendChild(glint);

    var gradients = [].slice.call(svg.querySelectorAll('linearGradient')).map(function (el) {
      return {
        el: el, y1: +el.getAttribute('y1'), y2: +el.getAttribute('y2'),
        y1s: el.getAttribute('y1'), y2s: el.getAttribute('y2'),
      };
    });

    var whisper = function (s, rot) {
      return 'translate(' + nose.x + ' ' + nose.y + ') rotate(' + rot + ')' +
        ' scale(' + s + ') translate(' + -nose.x + ' ' + -nose.y + ')';
    };

    // ---- 一帧（t: 进场 0..1，h: 悬停 0..1，it: 待机毫秒；it 为 null 就是静帧）----
    var resting = false;
    function frame(t, h, it) {
      var now = t * T;
      var idle = it != null;
      var last = FIRST + (holes.length - 1) * STAGGER + OPEN;
      var done = !idle && now >= last;
      if (done !== resting) {
        resting = done;
        // 停下来的那一帧交还给原路径：和 logo.svg 逐像素一致（reduced-motion 的静帧也是它）
        cut.style.display = done ? 'none' : '';
        moon.style.display = done ? '' : 'none';
      }
      if (done) {
        for (var k = 0; k < cuts.length; k++) cuts[k].setAttribute('r', 0);
      } else {
        for (var i2 = 0; i2 < holes.length; i2++) {
          var p = holes[i2];
          var s = clamp01((now - (FIRST + i2 * STAGGER)) / OPEN);
          var e = 1 - Math.pow(1 - s, 2.4); // 咬得快，然后收住
          // 从靠近老鼠的那一侧先破开，再往另一边让开——是咬，不是光圈
          var ux = (p.cx - nose.x) / p.d, uy = (p.cy - nose.y) / p.d;
          cuts[i2].setAttribute('r', round(p.r * e));
          cuts[i2].setAttribute('cx', round(p.cx - ux * p.r * (1 - e)));
          cuts[i2].setAttribute('cy', round(p.cy - uy * p.r * (1 - e)));
        }
        if (idle) {
          // 还在啃：波纹按啃食顺序在八个洞上走过去；幅度一秒多里长出来，接上静帧不跳
          var amp = 0.09 * Math.min(1, it / FADE);
          var u = (it / CHEW) % 1;
          for (var m2 = 0; m2 < holes.length; m2++) {
            var dd = Math.abs(u - m2 / holes.length);
            dd = Math.min(dd, 1 - dd);
            cuts[m2].setAttribute('r', round(holes[m2].r * (1 + amp * Math.exp(-(dd * dd) / 0.0125))));
          }
        }
      }
      var w = clamp01((now - W0) / WD);
      var ws = (0.02 + 0.98 * easeOutCubic(w)) * (1 + 0.06 * h);
      var rot = -7 * (1 - easeOutCubic(w)) - 1.6 * h;
      whiskers.forEach(function (el) { el.setAttribute('transform', whisper(ws, rot)); });
      var g = clamp01((now - G0) / GD);
      var wink = idle ? Math.pow(0.5 - 0.5 * Math.cos((it / WINK) * Math.PI * 2), 3) * 0.62 : 0;
      glint.setAttribute('opacity', Math.max(Math.sin(g * Math.PI) * 0.85, wink).toFixed(3));
      // 整枚标记的光极缓慢地移动（只动 logo 自己的那几个渐变）
      for (var g2 = 0; g2 < gradients.length; g2++) {
        var gr = gradients[g2];
        if (idle) {
          var d2 = Math.sin((it / BREATH_A) * Math.PI * 2) * 0.72 +
            Math.sin((it / BREATH_B) * Math.PI * 2 + 1.2) * 0.36;
          var off = (gr.y2 - gr.y1 || 1) * BREATH_AMP * d2;
          gr.el.setAttribute('y1', round(gr.y1 + off));
          gr.el.setAttribute('y2', round(gr.y2 + off));
        } else if (gr.el.getAttribute('y1') !== gr.y1s) {
          gr.el.setAttribute('y1', gr.y1s);
          gr.el.setAttribute('y2', gr.y2s);
        }
      }
    }

    // ---- 时钟 ---------------------------------------------------------------
    var raf = 0, hraf = 0, want = false, start = 0, idleMs = 0, prev = 0;
    var curT = 0, curH = 0, curI = null;
    var draw = function () { frame(curT, curH, curI); };
    function step(now) {
      if (!want) return;
      if (curT < 1) {
        var p = clamp01((now - start) / T);
        if (p >= 1) { curT = 1; idleMs = 0; prev = now; } else curT = p;
      } else {
        idleMs += prev ? Math.min(now - prev, 64) : 16; // 页面藏起来时 rAF 停了，待机时钟也就停了
        prev = now;
        curI = idleMs;
      }
      draw();
      raf = root.requestAnimationFrame(step);
    }
    function play() {
      want = false;
      root.cancelAnimationFrame(raf);
      if (reduced()) { curT = 1; curI = null; draw(); return; }
      curT = 0; curI = null; idleMs = 0; prev = 0; start = root.performance.now();
      want = true;
      draw();
      raf = root.requestAnimationFrame(step);
    }
    function stop() {
      want = false;
      root.cancelAnimationFrame(raf);
      root.cancelAnimationFrame(hraf);
    }
    function hover() {
      root.cancelAnimationFrame(hraf);
      if (reduced()) return;
      var t0 = root.performance.now();
      var walk = function (now) {
        // rAF 给的是这一帧的起始时刻，可能早于刚才取的 t0，所以先夹到 0
        var p = Math.max(0, (now - t0) / HOVER);
        curH = p >= 1 ? 0 : p < 0.42 ? p / 0.42 : 1 - (p - 0.42) / 0.58;
        draw();
        if (p < 1) hraf = root.requestAnimationFrame(walk);
      };
      hraf = root.requestAnimationFrame(walk);
    }
    function bite() {
      root.cancelAnimationFrame(hraf);
      if (reduced()) return;
      var t0 = root.performance.now();
      var walk = function (now) {
        var p = Math.max(0, (now - t0) / BITE);
        if (p >= 1) {
          biteCircle.setAttribute('r', 0);
          draw();
          return;
        }
        // 咬开、含住、咽下去（半径不能是负数，夹一道保险）
        var o = Math.max(0, p < 0.3 ? easeOutCubic(p / 0.3) : p < 0.62 ? 1 : 1 - (p - 0.62) / 0.38);
        biteCircle.setAttribute('r', (vb[2] * 0.0217 * o).toFixed(1));
        curH = Math.max(curH, Math.sin(clamp01((p - 0.28) / 0.5) * Math.PI) * 0.9);
        draw();
        hraf = root.requestAnimationFrame(walk);
      };
      hraf = root.requestAnimationFrame(walk);
    }

    var onVis = function () {
      if (document.hidden) root.cancelAnimationFrame(raf);
      else if (want) { prev = 0; raf = root.requestAnimationFrame(step); }
    };
    document.addEventListener('visibilitychange', onVis);
    svg.addEventListener('pointerenter', hover);
    svg.addEventListener('click', bite);

    var mq = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)');
    var onMq = function () { if (mq.matches) { stop(); curT = 1; curI = null; draw(); } };
    if (mq && mq.addEventListener) mq.addEventListener('change', onMq);

    var api = {
      play: play,
      stop: stop,
      bite: bite,
      // 静帧：任何时候调它都回到和 logo.svg 一模一样的那一帧
      still: function () {
        stop();
        curT = 1; curH = 0; curI = null;
        draw();
      },
      reducedMotion: reduced,
      // 只给自动化用：直接给某一帧（t/h/it），以及量出来的洞和鼻尖
      __test: { frame: frame, holes: holes, nose: nose, cuts: cuts },
      destroy: function () {
        stop();
        document.removeEventListener('visibilitychange', onVis);
        svg.removeEventListener('pointerenter', hover);
        svg.removeEventListener('click', bite);
        if (mq && mq.removeEventListener) mq.removeEventListener('change', onMq);
        if (cut.parentNode) cut.parentNode.removeChild(cut);
        cuts.forEach(function (c) { if (c.parentNode) c.parentNode.removeChild(c); });
        if (biteCircle.parentNode) biteCircle.parentNode.removeChild(biteCircle);
        if (glint.parentNode) glint.parentNode.removeChild(glint);
        gradients.forEach(function (gr) {
          gr.el.setAttribute('y1', gr.y1s);
          gr.el.setAttribute('y2', gr.y2s);
        });
        whiskers.forEach(function (el) { el.removeAttribute('transform'); });
        moon.style.display = '';
        svg.__logoMotion = null;
      },
    };
    svg.__logoMotion = api;
    api.still(); // 先落在静帧上，免得第一帧闪一下
    if (opts.auto !== false) play();
    return api;
  }

  root.logoMotion = { attach: attach };
})(typeof window !== 'undefined' ? window : globalThis);
