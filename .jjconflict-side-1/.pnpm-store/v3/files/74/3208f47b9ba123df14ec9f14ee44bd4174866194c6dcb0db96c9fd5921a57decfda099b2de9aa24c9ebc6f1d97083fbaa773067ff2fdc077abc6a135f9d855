import _ from "viewerjs";
import { default as on } from "viewerjs";
import { h as K, render as G, watch as x, nextTick as P, defineComponent as be, ref as me, onMounted as he, onUnmounted as we, openBlock as Te, createElementBlock as $e, renderSlot as Oe } from "vue";
var je = typeof global == "object" && global && global.Object === Object && global;
const re = je;
var _e = typeof self == "object" && self && self.Object === Object && self, Ae = re || _e || Function("return this")();
const E = Ae;
var xe = E.Symbol;
const S = xe;
var te = Object.prototype, Pe = te.hasOwnProperty, Se = te.toString, O = S ? S.toStringTag : void 0;
function Ie(e) {
  var r = Pe.call(e, O), t = e[O];
  try {
    e[O] = void 0;
    var n = !0;
  } catch {
  }
  var i = Se.call(e);
  return n && (r ? e[O] = t : delete e[O]), i;
}
var Ee = Object.prototype, Me = Ee.toString;
function Ve(e) {
  return Me.call(e);
}
var Ce = "[object Null]", Be = "[object Undefined]", H = S ? S.toStringTag : void 0;
function M(e) {
  return e == null ? e === void 0 ? Be : Ce : H && H in Object(e) ? Ie(e) : Ve(e);
}
function V(e) {
  return e != null && typeof e == "object";
}
var Ue = "[object Symbol]";
function Fe(e) {
  return typeof e == "symbol" || V(e) && M(e) == Ue;
}
var Ne = Array.isArray;
const De = Ne;
var We = /\s/;
function Le(e) {
  for (var r = e.length; r-- && We.test(e.charAt(r)); )
    ;
  return r;
}
var Re = /^\s+/;
function Ke(e) {
  return e && e.slice(0, Le(e) + 1).replace(Re, "");
}
function b(e) {
  var r = typeof e;
  return e != null && (r == "object" || r == "function");
}
var q = 0 / 0, Ge = /^[-+]0x[0-9a-f]+$/i, He = /^0b[01]+$/i, qe = /^0o[0-7]+$/i, Je = parseInt;
function J(e) {
  if (typeof e == "number")
    return e;
  if (Fe(e))
    return q;
  if (b(e)) {
    var r = typeof e.valueOf == "function" ? e.valueOf() : e;
    e = b(r) ? r + "" : r;
  }
  if (typeof e != "string")
    return e === 0 ? e : +e;
  e = Ke(e);
  var t = He.test(e);
  return t || qe.test(e) ? Je(e.slice(2), t ? 2 : 8) : Ge.test(e) ? q : +e;
}
function ne(e) {
  return e;
}
var Xe = "[object AsyncFunction]", ze = "[object Function]", Qe = "[object GeneratorFunction]", Ye = "[object Proxy]";
function ie(e) {
  if (!b(e))
    return !1;
  var r = M(e);
  return r == ze || r == Qe || r == Xe || r == Ye;
}
var Ze = E["__core-js_shared__"];
const F = Ze;
var X = function() {
  var e = /[^.]+$/.exec(F && F.keys && F.keys.IE_PROTO || "");
  return e ? "Symbol(src)_1." + e : "";
}();
function ke(e) {
  return !!X && X in e;
}
var er = Function.prototype, rr = er.toString;
function tr(e) {
  if (e != null) {
    try {
      return rr.call(e);
    } catch {
    }
    try {
      return e + "";
    } catch {
    }
  }
  return "";
}
var nr = /[\\^$.*+?()[\]{}|]/g, ir = /^\[object .+?Constructor\]$/, or = Function.prototype, ar = Object.prototype, ur = or.toString, sr = ar.hasOwnProperty, cr = RegExp(
  "^" + ur.call(sr).replace(nr, "\\$&").replace(/hasOwnProperty|(function).*?(?=\\\()| for .+?(?=\\\])/g, "$1.*?") + "$"
);
function fr(e) {
  if (!b(e) || ke(e))
    return !1;
  var r = ie(e) ? cr : ir;
  return r.test(tr(e));
}
function dr(e, r) {
  return e == null ? void 0 : e[r];
}
function lr(e, r) {
  var t = dr(e, r);
  return fr(t) ? t : void 0;
}
function pr(e, r, t) {
  switch (t.length) {
    case 0:
      return e.call(r);
    case 1:
      return e.call(r, t[0]);
    case 2:
      return e.call(r, t[0], t[1]);
    case 3:
      return e.call(r, t[0], t[1], t[2]);
  }
  return e.apply(r, t);
}
var gr = 800, vr = 16, yr = Date.now;
function br(e) {
  var r = 0, t = 0;
  return function() {
    var n = yr(), i = vr - (n - t);
    if (t = n, i > 0) {
      if (++r >= gr)
        return arguments[0];
    } else
      r = 0;
    return e.apply(void 0, arguments);
  };
}
function mr(e) {
  return function() {
    return e;
  };
}
var hr = function() {
  try {
    var e = lr(Object, "defineProperty");
    return e({}, "", {}), e;
  } catch {
  }
}();
const I = hr;
var wr = I ? function(e, r) {
  return I(e, "toString", {
    configurable: !0,
    enumerable: !1,
    value: mr(r),
    writable: !0
  });
} : ne;
const Tr = wr;
var $r = br(Tr);
const Or = $r;
var jr = 9007199254740991, _r = /^(?:0|[1-9]\d*)$/;
function oe(e, r) {
  var t = typeof e;
  return r = r == null ? jr : r, !!r && (t == "number" || t != "symbol" && _r.test(e)) && e > -1 && e % 1 == 0 && e < r;
}
function ae(e, r, t) {
  r == "__proto__" && I ? I(e, r, {
    configurable: !0,
    enumerable: !0,
    value: t,
    writable: !0
  }) : e[r] = t;
}
function W(e, r) {
  return e === r || e !== e && r !== r;
}
var Ar = Object.prototype, xr = Ar.hasOwnProperty;
function ue(e, r, t) {
  var n = e[r];
  (!(xr.call(e, r) && W(n, t)) || t === void 0 && !(r in e)) && ae(e, r, t);
}
function Pr(e, r, t, n) {
  var i = !t;
  t || (t = {});
  for (var u = -1, c = r.length; ++u < c; ) {
    var a = r[u], d = n ? n(t[a], e[a], a, t, e) : void 0;
    d === void 0 && (d = e[a]), i ? ae(t, a, d) : ue(t, a, d);
  }
  return t;
}
var z = Math.max;
function Sr(e, r, t) {
  return r = z(r === void 0 ? e.length - 1 : r, 0), function() {
    for (var n = arguments, i = -1, u = z(n.length - r, 0), c = Array(u); ++i < u; )
      c[i] = n[r + i];
    i = -1;
    for (var a = Array(r + 1); ++i < r; )
      a[i] = n[i];
    return a[r] = t(c), pr(e, this, a);
  };
}
function se(e, r) {
  return Or(Sr(e, r, ne), e + "");
}
var Ir = 9007199254740991;
function ce(e) {
  return typeof e == "number" && e > -1 && e % 1 == 0 && e <= Ir;
}
function C(e) {
  return e != null && ce(e.length) && !ie(e);
}
function fe(e, r, t) {
  if (!b(t))
    return !1;
  var n = typeof r;
  return (n == "number" ? C(t) && oe(r, t.length) : n == "string" && r in t) ? W(t[r], e) : !1;
}
function Er(e) {
  return se(function(r, t) {
    var n = -1, i = t.length, u = i > 1 ? t[i - 1] : void 0, c = i > 2 ? t[2] : void 0;
    for (u = e.length > 3 && typeof u == "function" ? (i--, u) : void 0, c && fe(t[0], t[1], c) && (u = i < 3 ? void 0 : u, i = 1), r = Object(r); ++n < i; ) {
      var a = t[n];
      a && e(r, a, n, u);
    }
    return r;
  });
}
var Mr = Object.prototype;
function L(e) {
  var r = e && e.constructor, t = typeof r == "function" && r.prototype || Mr;
  return e === t;
}
function Vr(e, r) {
  for (var t = -1, n = Array(e); ++t < e; )
    n[t] = r(t);
  return n;
}
var Cr = "[object Arguments]";
function Q(e) {
  return V(e) && M(e) == Cr;
}
var de = Object.prototype, Br = de.hasOwnProperty, Ur = de.propertyIsEnumerable, Fr = Q(function() {
  return arguments;
}()) ? Q : function(e) {
  return V(e) && Br.call(e, "callee") && !Ur.call(e, "callee");
};
const Nr = Fr;
function Dr() {
  return !1;
}
var le = typeof exports == "object" && exports && !exports.nodeType && exports, Y = le && typeof module == "object" && module && !module.nodeType && module, Wr = Y && Y.exports === le, Z = Wr ? E.Buffer : void 0, Lr = Z ? Z.isBuffer : void 0, Rr = Lr || Dr;
const Kr = Rr;
var Gr = "[object Arguments]", Hr = "[object Array]", qr = "[object Boolean]", Jr = "[object Date]", Xr = "[object Error]", zr = "[object Function]", Qr = "[object Map]", Yr = "[object Number]", Zr = "[object Object]", kr = "[object RegExp]", et = "[object Set]", rt = "[object String]", tt = "[object WeakMap]", nt = "[object ArrayBuffer]", it = "[object DataView]", ot = "[object Float32Array]", at = "[object Float64Array]", ut = "[object Int8Array]", st = "[object Int16Array]", ct = "[object Int32Array]", ft = "[object Uint8Array]", dt = "[object Uint8ClampedArray]", lt = "[object Uint16Array]", pt = "[object Uint32Array]", f = {};
f[ot] = f[at] = f[ut] = f[st] = f[ct] = f[ft] = f[dt] = f[lt] = f[pt] = !0;
f[Gr] = f[Hr] = f[nt] = f[qr] = f[it] = f[Jr] = f[Xr] = f[zr] = f[Qr] = f[Yr] = f[Zr] = f[kr] = f[et] = f[rt] = f[tt] = !1;
function gt(e) {
  return V(e) && ce(e.length) && !!f[M(e)];
}
function vt(e) {
  return function(r) {
    return e(r);
  };
}
var pe = typeof exports == "object" && exports && !exports.nodeType && exports, j = pe && typeof module == "object" && module && !module.nodeType && module, yt = j && j.exports === pe, N = yt && re.process, bt = function() {
  try {
    var e = j && j.require && j.require("util").types;
    return e || N && N.binding && N.binding("util");
  } catch {
  }
}();
const k = bt;
var ee = k && k.isTypedArray, mt = ee ? vt(ee) : gt;
const ht = mt;
var wt = Object.prototype, Tt = wt.hasOwnProperty;
function ge(e, r) {
  var t = De(e), n = !t && Nr(e), i = !t && !n && Kr(e), u = !t && !n && !i && ht(e), c = t || n || i || u, a = c ? Vr(e.length, String) : [], d = a.length;
  for (var s in e)
    (r || Tt.call(e, s)) && !(c && (s == "length" || i && (s == "offset" || s == "parent") || u && (s == "buffer" || s == "byteLength" || s == "byteOffset") || oe(s, d))) && a.push(s);
  return a;
}
function $t(e, r) {
  return function(t) {
    return e(r(t));
  };
}
var Ot = $t(Object.keys, Object);
const jt = Ot;
var _t = Object.prototype, At = _t.hasOwnProperty;
function xt(e) {
  if (!L(e))
    return jt(e);
  var r = [];
  for (var t in Object(e))
    At.call(e, t) && t != "constructor" && r.push(t);
  return r;
}
function Pt(e) {
  return C(e) ? ge(e) : xt(e);
}
var St = Object.prototype, It = St.hasOwnProperty, Et = Er(function(e, r) {
  if (L(r) || C(r)) {
    Pr(r, Pt(r), e);
    return;
  }
  for (var t in r)
    It.call(r, t) && ue(e, t, r[t]);
});
const Mt = Et;
function Vt(e) {
  var r = [];
  if (e != null)
    for (var t in Object(e))
      r.push(t);
  return r;
}
var Ct = Object.prototype, Bt = Ct.hasOwnProperty;
function Ut(e) {
  if (!b(e))
    return Vt(e);
  var r = L(e), t = [];
  for (var n in e)
    n == "constructor" && (r || !Bt.call(e, n)) || t.push(n);
  return t;
}
function Ft(e) {
  return C(e) ? ge(e, !0) : Ut(e);
}
var Nt = function() {
  return E.Date.now();
};
const D = Nt;
var Dt = "Expected a function", Wt = Math.max, Lt = Math.min;
function Rt(e, r, t) {
  var n, i, u, c, a, d, s = 0, m = !1, o = !1, l = !0;
  if (typeof e != "function")
    throw new TypeError(Dt);
  r = J(r) || 0, b(t) && (m = !!t.leading, o = "maxWait" in t, u = o ? Wt(J(t.maxWait) || 0, r) : u, l = "trailing" in t ? !!t.trailing : l);
  function g(p) {
    var y = n, $ = i;
    return n = i = void 0, s = p, c = e.apply($, y), c;
  }
  function v(p) {
    return s = p, a = setTimeout(T, r), m ? g(p) : c;
  }
  function h(p) {
    var y = p - d, $ = p - s, R = r - y;
    return o ? Lt(R, u - $) : R;
  }
  function w(p) {
    var y = p - d, $ = p - s;
    return d === void 0 || y >= r || y < 0 || o && $ >= u;
  }
  function T() {
    var p = D();
    if (w(p))
      return A(p);
    a = setTimeout(T, h(p));
  }
  function A(p) {
    return a = void 0, l && n ? g(p) : (n = i = void 0, c);
  }
  function B() {
    a !== void 0 && clearTimeout(a), s = 0, n = d = i = a = void 0;
  }
  function ye() {
    return a === void 0 ? c : A(D());
  }
  function U() {
    var p = D(), y = w(p);
    if (n = arguments, i = this, d = p, y) {
      if (a === void 0)
        return v(d);
      if (o)
        return clearTimeout(a), a = setTimeout(T, r), g(d);
    }
    return a === void 0 && (a = setTimeout(T, r)), c;
  }
  return U.cancel = B, U.flush = ye, U;
}
var ve = Object.prototype, Kt = ve.hasOwnProperty, Gt = se(function(e, r) {
  e = Object(e);
  var t = -1, n = r.length, i = n > 2 ? r[2] : void 0;
  for (i && fe(r[0], r[1], i) && (n = 1); ++t < n; )
    for (var u = r[t], c = Ft(u), a = -1, d = c.length; ++a < d; ) {
      var s = c[a], m = e[s];
      (m === void 0 || W(m, ve[s]) && !Kt.call(e, s)) && (e[s] = u[s]);
    }
  return e;
});
const Ht = Gt, qt = ({ images: e = [], options: r }) => {
  r = Mt(r, {
    inline: !1
  });
  const t = document.createElement("div"), n = K(
    "div",
    {
      style: {
        display: "none"
      },
      class: ["__viewer-token"]
    },
    e.map((a) => K(
      "img",
      typeof a == "string" ? { src: a } : a
    ))
  );
  G(n, t);
  const i = t.firstElementChild;
  document.body.appendChild(i);
  const u = new _(i, r), c = u.destroy.bind(u);
  return u.destroy = function() {
    return c(), G(null, t), u;
  }, u.show(), i.addEventListener("hidden", function() {
    this.viewer === u && u.destroy();
  }), u;
}, Jt = ({ name: e = "viewer", debug: r = !1 } = {}) => {
  async function t(o, l, g = !1, v = !1) {
    await P(), !(v && !n(o)) && (g || !o[`$${e}`] ? (c(o), o[`$${e}`] = new _(o, l), s("Viewer created")) : (o[`$${e}`].update(), s("Viewer updated")));
  }
  function n(o) {
    const l = o.innerHTML.match(/<img([\w\W]+?)[\\/]?>/g), g = l ? l.join("") : void 0;
    return o.__viewerImageDiffCache === g ? (s("Element change detected, but image(s) has not changed"), !1) : (s("Image change detected"), o.__viewerImageDiffCache = g, !0);
  }
  function i(o, l, g, v) {
    a(o);
    const h = window.MutationObserver || window.WebKitMutationObserver || window.MozMutationObserver;
    if (!h) {
      s("Observer not supported");
      return;
    }
    const w = new h((A) => {
      A.forEach((B) => {
        s(`Viewer mutation:${B.type}`), g(o, l, v, !0);
      });
    }), T = { attributes: !0, childList: !0, characterData: !0, subtree: !0 };
    w.observe(o, T), o.__viewerMutationObserver = w, s("Observer created");
  }
  function u(o, l, g, v) {
    o.__viewerUnwatch = x(() => l.value, (h, w) => {
      s("Change detected by watcher: ", l.value), v(o, h, !0, !1);
    }, { deep: !0 }), s("Watcher created, expression: ", l.value);
  }
  function c(o) {
    !o[`$${e}`] || (o[`$${e}`].destroy(), delete o[`$${e}`], s("Viewer destroyed"));
  }
  function a(o) {
    !o.__viewerMutationObserver || (o.__viewerMutationObserver.disconnect(), delete o.__viewerMutationObserver, s("observer destroyed"));
  }
  function d(o) {
    !o.__viewerUnwatch || (o.__viewerUnwatch(), delete o.__viewerUnwatch, s("Watcher destroyed"));
  }
  function s(...o) {
    r && console.log(...o);
  }
  return {
    mounted(o, l, g) {
      s("Viewer bind");
      const v = Rt(t, 50);
      v(o, l.value), u(o, l, g, v), l.modifiers.static || i(o, l.value, v, l.modifiers.rebuild);
    },
    unmounted(o) {
      s("Viewer unbind"), a(o), d(o), c(o);
    }
  };
}, Xt = be({
  name: "Viewer",
  props: {
    images: {
      type: Array,
      default: () => []
    },
    rebuild: {
      type: Boolean,
      default: !1
    },
    trigger: {
      type: Object,
      default: null
    },
    options: {
      type: Object,
      default: () => null
    }
  },
  emits: ["inited"],
  setup(e, { emit: r }) {
    let t;
    const n = me();
    function i() {
      t = new _(n.value, e.options), r("inited", t);
    }
    function u() {
      t && t.destroy();
    }
    function c() {
      u(), i();
    }
    function a() {
      t ? (t.update(), r("inited", t)) : i();
    }
    function d() {
      e.rebuild ? c() : a();
    }
    const s = { deep: !0 };
    return x(() => e.images, () => {
      P(() => {
        d();
      });
    }, s), x(() => e.trigger, () => {
      P(() => {
        d();
      });
    }, s), x(() => e.options, () => {
      P(() => {
        c();
      });
    }, s), he(() => i()), we(() => u()), {
      root: n,
      createViewer: i,
      rebuildViewer: c,
      updateViewer: a,
      destroyViewer: u
    };
  }
}), zt = (e, r) => {
  const t = e.__vccOpts || e;
  for (const [n, i] of r)
    t[n] = i;
  return t;
}, Qt = { ref: "root" };
function Yt(e, r, t, n, i, u) {
  return Te(), $e("div", Qt, [
    Oe(e.$slots, "default", {
      images: e.images,
      options: e.options
    })
  ], 512);
}
const Zt = /* @__PURE__ */ zt(Xt, [["render", Yt]]), rn = {
  install(e, { name: r = "viewer", debug: t = !1, defaultOptions: n } = {}) {
    n && _.setDefaults(n), e.config.globalProperties[`$${r}Api`] = qt, e.component(r, Ht(Zt, { name: r })), e.directive(r, Jt({ name: r, debug: t }));
  },
  setDefaults(e) {
    _.setDefaults(e);
  }
};
export {
  on as Viewer,
  qt as api,
  Zt as component,
  rn as default,
  Jt as directive
};
