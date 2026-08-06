import { defineComponent as h, openBlock as l, createElementBlock as v, Fragment as P, createVNode as i, unref as t, renderSlot as A, ref as y, watch as w, onMounted as C, onBeforeUnmount as $, createBlock as c, mergeProps as u, withCtx as d, createElementVNode as f, normalizeClass as M, renderList as S, createCommentVNode as m, toDisplayString as b, createTextVNode as D, normalizeProps as L, withDirectives as O, vShow as j, markRaw as H, h as N } from "vue";
import { Toaster as _, toast as B } from "vue-sonner";
import { VProgressLinear as z, VCard as E, VCardText as F, VAvatar as k, VImg as T, VProgressCircular as V, VIcon as K, VCardActions as R, VSpacer as U, VBtn as W, VExpandTransition as q } from "vuetify/components";
const ne = /* @__PURE__ */ h({
  __name: "VSonner",
  props: {
    invert: { type: Boolean },
    position: { default: "bottom-center" },
    hotkey: { default: () => ["altKey", "KeyT"] },
    expand: { type: Boolean, default: !1 },
    duration: {},
    gap: {},
    visibleToasts: { default: 3 },
    toastOptions: {},
    class: {},
    offset: { default: 32 },
    dir: {},
    icons: {},
    containerAriaLabel: {},
    pauseWhenPageIsHidden: { type: Boolean },
    cn: {}
  },
  setup(o) {
    return (r, e) => (l(), v(P, null, [
      i(t(_), {
        position: r.position,
        hotkey: r.hotkey,
        expand: r.expand,
        "visible-toasts": r.visibleToasts,
        duration: r.duration,
        "toast-options": r.toastOptions,
        offset: r.offset
      }, null, 8, ["position", "hotkey", "expand", "visible-toasts", "duration", "toast-options", "offset"]),
      A(r.$slots, "default")
    ], 64));
  }
}), G = /* @__PURE__ */ h({
  __name: "ProgressBar",
  props: {
    duration: { default: 5e3 },
    progressBarProps: {},
    isPaused: { type: Boolean, default: !1 },
    reverseProgressBar: { type: Boolean, default: !1 }
  },
  setup(o) {
    const r = o, e = r.reverseProgressBar ? y(0) : y(5e3);
    let s;
    w(() => r.isPaused, (n) => {
      var g;
      !n && !((g = r.progressBarProps) != null && g.indeterminate) && a();
    });
    function a() {
      s = setTimeout(() => {
        r.isPaused || (r.reverseProgressBar ? e.value += 120 : e.value -= 120, e.value <= 0 && !r.reverseProgressBar ? (e.value = 0, clearInterval(s)) : e.value >= r.duration && r.reverseProgressBar ? (e.value = r.duration, clearInterval(s)) : a());
      }, 100);
    }
    return C(() => {
      var n;
      (n = r.progressBarProps) != null && n.indeterminate || (e.value = r.reverseProgressBar ? 0 : r.duration, a());
    }), $(() => {
      clearInterval(s);
    }), (n, g) => (l(), c(t(z), u(r.progressBarProps, {
      "model-value": Math.floor(100 * (t(e) / r.duration))
    }), null, 16, ["model-value"]));
  }
}), J = { class: "d-flex align-center justify-center fill-height" }, Q = {
  key: 1,
  class: "d-flex align-center mr-8"
}, X = { class: "d-flex align-center justify-center fill-height" }, Y = { key: 3 }, Z = { class: "pb-1" }, x = ["innerHTML"], ee = /* @__PURE__ */ h({
  inheritAttrs: !1,
  __name: "Toast",
  props: {
    text: {},
    description: {},
    vertical: { type: Boolean, default: !1 },
    cardProps: {},
    cardTextProps: {},
    cardActionsProps: { default: () => ({}) },
    action: {},
    prependIcon: {},
    prependIconProps: {},
    avatar: {},
    multipleAvatars: {},
    avatarProps: {},
    progressBar: { type: Boolean, default: !1 },
    reverseProgressBar: { type: Boolean },
    progressDuration: { default: 5e3 },
    progressBarProps: {},
    loading: { type: Boolean }
  },
  emits: ["closeToast"],
  setup(o) {
    const r = y(!1);
    return (e, s) => (l(), c(t(E), u({ class: "card-snackbar" }, e.cardProps, {
      onMouseenter: s[1] || (s[1] = (a) => r.value = !0),
      onMouseleave: s[2] || (s[2] = (a) => r.value = !1)
    }), {
      default: d(() => [
        f("div", {
          class: M({ "d-flex flex-no-wrap justify-space-between": !e.vertical })
        }, [
          i(t(F), u(e.cardTextProps, {
            class: { "d-flex align-center": e.prependIcon || e.avatar || e.multipleAvatars }
          }), {
            default: d(() => [
              e.avatar ? (l(), c(t(k), u({
                key: 0,
                class: "mr-2"
              }, e.avatarProps), {
                default: d(() => [
                  i(t(T), {
                    src: e.avatar,
                    cover: ""
                  }, {
                    placeholder: d(() => [
                      f("div", J, [
                        i(t(V), {
                          color: "grey-lighten-2",
                          indeterminate: ""
                        })
                      ])
                    ]),
                    _: 1
                  }, 8, ["src"])
                ]),
                _: 1
              }, 16)) : e.multipleAvatars ? (l(), v("div", Q, [
                (l(!0), v(P, null, S(e.multipleAvatars.length <= 5 ? e.multipleAvatars : e.multipleAvatars.slice(0, 5), (a, n) => (l(), c(t(k), u({ ref_for: !0 }, e.avatarProps, {
                  key: n,
                  class: "mr-n6",
                  style: { zIndex: 5 - n }
                }), {
                  default: d(() => [
                    i(t(T), {
                      src: a,
                      cover: ""
                    }, {
                      placeholder: d(() => [
                        f("div", X, [
                          i(t(V), {
                            color: "grey-lighten-2",
                            indeterminate: ""
                          })
                        ])
                      ]),
                      _: 2
                    }, 1032, ["src"])
                  ]),
                  _: 2
                }, 1040, ["style"]))), 128))
              ])) : m("", !0),
              e.prependIcon ? (l(), c(t(K), u({
                key: 2,
                class: "mr-2",
                icon: e.prependIcon
              }, e.prependIconProps), null, 16, ["icon"])) : m("", !0),
              e.description ? (l(), v("div", Y, [
                f("div", Z, b(e.text), 1),
                f("p", {
                  class: "font-weight-light",
                  innerHTML: e.description
                }, null, 8, x)
              ])) : (l(), v(P, { key: 4 }, [
                D(b(e.text), 1)
              ], 64))
            ]),
            _: 1
          }, 16, ["class"]),
          e.action ? (l(), c(t(R), L(u({ key: 0 }, e.cardActionsProps)), {
            default: d(() => [
              i(t(U)),
              i(t(W), u(e.action.buttonProps, {
                text: e.action.label,
                onClick: s[0] || (s[0] = () => {
                  var a, n;
                  e.$emit("closeToast"), (n = (a = e.action) == null ? void 0 : a.onClick) == null || n.call(a);
                })
              }), null, 16, ["text"])
            ]),
            _: 1
          }, 16)) : m("", !0)
        ], 2),
        i(t(q), null, {
          default: d(() => [
            O(i(G, {
              duration: e.progressDuration,
              "progress-bar-props": e.progressBarProps,
              "is-paused": r.value,
              "reverse-progress-bar": e.reverseProgressBar
            }, null, 8, ["duration", "progress-bar-props", "is-paused", "reverse-progress-bar"]), [
              [j, e.progressBar]
            ])
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 16));
  }
}), re = (o, r) => {
  const e = o.__vccOpts || o;
  for (const [s, a] of r)
    e[s] = a;
  return e;
}, se = /* @__PURE__ */ re(ee, [["__scopeId", "data-v-c014bbd8"]]);
function I(o, r) {
  const { description: e, action: s, ...a } = r || {};
  return B.custom(H(N(se, {
    ...a,
    progressBar: (r == null ? void 0 : r.progressBar) ?? !1,
    progressDuration: (r == null ? void 0 : r.duration) ?? 5e3,
    progressBarProps: {
      ...r == null ? void 0 : r.progressBarProps,
      indeterminate: r == null ? void 0 : r.loading
    },
    description: e,
    action: s,
    text: o
  })), {
    ...a,
    unstyled: !0
  });
}
function p(o, r) {
  return function(e, s) {
    return I(e, {
      prependIcon: r,
      cardProps: {
        color: o,
        ...s == null ? void 0 : s.cardProps
      },
      ...s
    });
  };
}
const le = Object.assign(I, {
  success: p("success", "mdi-check-circle"),
  error: p("error", "mdi-cancel"),
  warning: p("warning", "mdi-alert"),
  info: p("info", "mdi-alert-circle"),
  primary: p("primary", "mdi-bell"),
  secondary: p("secondary", "mdi-bell"),
  dismiss(o) {
    return B.dismiss(o);
  },
  toastOriginal: B
});
export {
  ne as VSonner,
  le as toast
};
//# sourceMappingURL=vuetify-sonner.js.map
