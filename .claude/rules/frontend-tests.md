---
paths:
  - "frontend/src/**/*.spec.ts"
  - "frontend/scripts/tsc-ratchet.mjs"
  - "frontend/tsconfig.json"
---

# Frontend tests — test the rendered and observable behaviour

## exposed-of-root-element-is-the-child-not-the-host

子组件被桩替掉之后，DOM 根元素是桩画的，`el.__vueParentComponent` 就是桩的实例；要测的宿组件是它的上一层（`.parent`）。取错这一层，用例会静默地验桩而不是验宿主——把宿主的 `defineExpose` 整个掏空也照样绿。

**Why:** 2026-10-06 给 `PanelOverviewHost` 补接线用例（PR #2916）时第一版就是这么写的，四条全绿，CI 只报出一处无关的类型错；直到拿变异试（掏空宿主的 `pulse`）才发现第四条什么都没测。`PreviewPages.spec.ts` 里那个取法成立的前提是「根元素归这个组件自己」，换成桩就不成立。

**How to apply:** 宿主类组件（组装层，把子组件当根渲染）的 spec 要拿它 `defineExpose` 的东西时，从元素的实例往上一层取；写完拿一条变异（掏空宿主暴露的某个方法）确认这条用例真会红，别只看它绿。

## dompurify-tests-need-jsdom

前端任何要断言「DOMPurify 清理之后剩什么」的测试，都要在该 spec 顶部写
`// @vitest-environment jsdom`。仓库默认的 happy-dom 下 DOMPurify 3.2.6 会静默给出
错误结果，清理断言会因为「输出是空串」而全部通过，看不出清理是真是假。

实测（2026-10-08，frontend/src/utils/parser.spec.ts）：

- happy-dom 里 `DOMPurify.sanitize('<p><script>alert(1)</script>正文</p>')` 返回 `''`；
  jsdom 和真实浏览器返回 `<p>正文</p>`。
- happy-dom 里只含 MathML 的输入也被清成 `''`；jsdom 下默认策略其实保留 MathML。
- 只要输入里带 `<script>`，happy-dom 下整段返回空串 —— 于是「危险内容已被清掉」这类
  断言全绿。

**Why:** 本仓库 vitest 默认环境是 happy-dom（见 frontend/vite.config.ts 的 `test.environment`），
只有 doc 系列 spec 显式切到 jsdom；DOMPurify 是目前已知唯一在 happy-dom 下会这样
静默出错的依赖，出错的形态恰好是「安全断言假绿」，最坏的一种。

**How to apply:** 新增或改动「sanitize 后剩什么」的断言时，给该文件加
`// @vitest-environment jsdom`，并留一行注释说明为什么不能用 happy-dom，免得后来者
为统一环境把它改回去。

## assert-copy-by-parts-not-whole-line

测「这句话说得对不对」时，不要 `toBe('整句话')`——断它必须说出的那几件事：哪条 token / 哪个数字 / 哪个倍数。数字和倍数从代码里那一份常量取（`MOTION_FACTOR.slow`、`BASE_MS`），不要同样写死。

**Why:** 王长鑫 2026-10-12 审阅 #3498 时提的（「测试别按整句文案断言」）。同一句话那轮就改过两次（「4 倍」写死改掉、减少动态效果时慢放档的说明补上），整句比较的断言每次都跟着红一次，而它想守的东西一次都没变。整句断言挡不住真错，只挡改文案。

**How to apply:** 说数值的那类句子尤其要这样写：`expect(note).toContain(`${BASE_MS * MOTION_FACTOR.slow}ms`)` 这种。写死在测试里的数字/倍数，改代码时要一起改，否则测试反而是负担。判据是「这句必须说出的东西是什么」，不是「它现在长什么样」。

## narrow-tsc-to-read-ratchet-errors

`.github` 的类型检查是 `frontend/scripts/tsc-ratchet.mjs`（`pnpm run typecheck`）：跑全项目
`vue-tsc --noEmit`，报告里只有每个文件几条错（`src/lib/*.spec.ts: 0 -> 3`），`--json` 也一样，
没有开关能给诊断原文。

要看「哪一行、什么错」，在 `frontend/` 下临时放一份 tsconfig，写
`"extends": "./tsconfig.json"`、`files: ["<目标文件>"]`、`include: ["./src/**/*.d.ts"]`、
`exclude: []`、`references: []`，再跑 `node scripts/tsc-ratchet.mjs --project <那份配置>`。
漏掉那条 `include` 会把 `src/vite-env.d.ts` 关在程序外，报一堆假的 `ImportMeta.env` 缺失；
配置别放 `/var/tmp`——`types: ["vuetify"]` 按配置所在目录找 `node_modules`，会报
「Cannot find type definition file」。看完删掉临时文件，别提交。

窄跑也有跑不动的时候：被查的文件一旦引到预览站那整棵树（`views/demo/catalogFixtures.ts`、
`catalogChat.ts` 这层），单文件也 OOM，那就直说本机核不了这两档。
2026-10-08 实测：窄跑复现出 CI 报的那 3 条（`@/i18n` 的 `t` 传进收 `Trans` 的函数），
修完零诊断、CI 转绿。

**Why:** 本机跑不了整个 `vue-tsc`（见沙箱 3 GB 上限那条），闸门又只报条数，不绕就只能猜。
**How to apply:** 「类型闸门红了但不知道哪一行」时用这招。

## spa-token-injection-needs-refresh-stub

在 okcheese.com（或任何 dev 实例）做真机界面核验时，把 curl 拿到的 `accessToken`
写进 `localStorage.accessToken`（键就是这个名字）**不够**：SPA 启动会拿 refresh
cookie 去打 `POST /api/users/auth/refresh`。浏览器里没有那个 cookie，于是 401，
会话模块按「签出」处理并清掉 token，之后每个接口都 401、页面掉回登录页。

**绕法**：`context.route('**/api/users/auth/refresh', …)` 直接 fulfill 登录响应里
那对 `{accessToken, user}`。之后页面正常。

**Why:** 2026-10-02 我在工作机上实测 #944 的三个新页面，第一轮整页 401 加
「Your session expired」，看着像 token 失效；其实 token 全程有效——同一时刻用
同一个 token 手写 fetch 打 `/api/users/me` 和 `/api/spaces/651/materials` 都是 200。

**How to apply:** 走这条路先 stub refresh；排查 401 时先在手写 fetch 里验 token，
再怀疑存储键或格式。

## browser-timing-must-exclude-click-ripple

用 MutationObserver 量「点击到内容落定」时，Vuetify 的 v-ripple 会在点完后约 550ms 删掉自己的容器 `span.v-ripple__container`。这一次变动记进去，每个场景的「末次变动」都被拉到 545ms 附近——一段固定 500ms 的动画尾巴，与内容无关。2026-10-08 实测：后台「性能」页真实内容 250ms 落定，加上涟漪尾巴就成了 547ms。

**Why:** 不看这一条会得出「所有页面都要 0.5 秒」的假结论，还会把场景之间的差异抹平——5 次结果挤在 [542,548] 这种极窄范围，就是这种假象的指纹，正常的内容渲染不会这么整齐。

**How to apply:** 在 MutationObserver 里判 chrome：属性或文本变动看目标类名，childList 看增删的节点是不是全都带 ripple / overlay__scrim / v-click-outside；是就单独计数，不进内容变动。判据用「目标命中 或 增删节点全部命中」，别只看目标——涟漪容器被删时，目标往往是正常的列表项。

另一半是 t=0 放在哪：要在页内捕获阶段的 click 监听器里记时刻，别在 Playwright 的 `click()` 调用前记。`click()` 的可操作性检查会先跑 100–160ms，那段会被算进每一次测量。
