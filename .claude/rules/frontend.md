---
paths:
  - "frontend/src/**"
  - "frontend/index.html"
  - "frontend/*-baseline.json"
  - "frontend/package.json"
  - "frontend/pnpm-lock.yaml"
---

# Frontend — the rules you will otherwise get wrong on turn one

Full spec: [`docs/design-system.md`](../../docs/design-system.md). Read it before
any non-trivial styling work. This file is only the part that bites on turn one —
where it names a section of that spec, the spec is the authority, this is the
reminder.

The gates pass on a tree that still holds violations: the existing ones are
frozen in a baseline and only NEW ones are blocked. So a green gate does not
mean the file open in front of you is clean — it may well be one of the frozen
ones. `frontend/stylelint-baseline.json` and `frontend/palette-baseline.json`
say which files have an allowance; check there before copying a neighbour. After
a change, `frontend-design` (a skill) walks the whole checklist including the
parts no gate can see.

## The product has a dark theme. Every colour you write must survive it.

- **Never a colour literal in CSS, and never a Vuetify palette NAME** — the two
  halves of the same defect. `#757575` / `rgb(...)` / `hsl(...)`, and with no `#`
  at all `color="grey-lighten-5"` / `class="bg-white"` / `color="white"`, all
  render the same in both themes and are therefore wrong in one of them. The
  fixed-palette names read like semantic colours, which is why they shipped:
  seven of them sat on the global shell (rail, title bar, mobile bars, `v-main`),
  so when the dark theme landed the shell stayed near-white while the text on it
  followed `on-surface` and went pale grey — unreadable, on every page. Use
  `var(--text)` / `var(--muted)` / `var(--surface)` in CSS; use `background` /
  `surface` / `surface-light` / `surface-bright` / `on-surface-variant` /
  `primary` in Vuetify, **including in `withDefaults` prop defaults**. Both
  halves are gates (see the ratchets below). Replacement table: §1.2.
  - Exception, and write the reason in the code when you take it: where the
    BACKGROUND is itself theme-invariant, the ink on it must be too. The default
    avatar's background is the `#rrggbb` `avatarColor()` computes at a fixed
    OKLCH lightness (L = 0.54 / C = 0.12) — the same value in both themes, so
    its `#fff` text is correct and "fixing" it to a token breaks it.
- The neutral ramp is `--ink` (titles) > `--text` (body) > `--muted` (secondary).
  Pick by how important the information is, not by how it looks. `--faint` is not a
  text colour: it is decoration — icons, dividers, placeholder shapes. It clears the
  3:1 graphics floor but never reaches 4.5:1, so text a reader reads goes to `--muted`
  (§2.6 对比度).
- The frame (rail, sidebars, top bar) is `--canvas`; the content pane is one
  `--surface`, so content groups by spacing and `--line`, not by white cards on
  grey. Cards get a `--line` border and **no shadow** — `--shadow-1` and
  `--shadow-2` are for menus, dialogs and drawers only (§1.4, §3.4).
- **Status colours come in threes and are not interchangeable**: `--danger` is
  the MARK (dot, border, icon), `--danger-ink` is the TEXT, `--danger-wash` is
  the BACKGROUND. Same shape for `--ok` and `--accent`. Using the mark colour for
  text is the single most common defect and fails contrast in light theme (§1.5).
- Amber (`--accent`) is reserved for the main action of an area, unread markers,
  the current nav position, the brand mark, and an @ that notified someone
  (§1.6). A page may have several
  areas, but a group of sibling buttons has only one amber button. Not avatars,
  not status chips, not ordinary icons — ~95% of any screen is neutral.

If you find yourself writing `:root[data-theme='dark'] .thing { ... }`, stop:
nine times out of ten the real fix is that `.thing` picked the wrong token.

## Motion: it moves only to explain a change

Full rules: [`docs/design-system.md` §9](../../docs/design-system.md#9-动效) —
what motion is for, the duration and easing tokens and which to use, `:hover`
changing colour and never position, and why `transition: all` stays out. The one
that still gets written wrong after reading all of that:

- **Anything `infinite` needs its own reduced-motion escape.** The global
  fallback in `style.css` squeezes durations to `0.001ms`, which turns a 1.6s
  pulse into a strobe — worse than leaving it. Write
  `@media (prefers-reduced-motion: reduce) { animation: none }` next to it, and
  make sure the thing still says what it meant with the animation off.

## Colours live in two files and must be changed in both

`frontend/src/style.css` (CSS variables, for hand-written CSS) and
`frontend/src/plugins/vuetify.ts` (Vuetify theme, for components) are mirrors of
one palette, each with a `light` and a `dark` set. Editing one and not the other
produces "some things changed and some didn't", which is painful to trace.

Same rule for the theme boot script: the inline `<script>` in
`frontend/index.html` deliberately duplicates six lines of `src/theme.ts` to
avoid a white flash before first paint. Change the storage key
(`cheesex.theme`) or the address-bar colours in one, change the other.

## Scales

Font size, line height and spacing are in
[`docs/design-system.md` §3](../../docs/design-system.md#3-尺度); radius is a
four-step ladder stylelint enforces — `--radius-sm` 6 / `--radius-md` 8 /
`--radius-lg` 12 / `--radius-pill` 999, nothing else, picked by what the element
is and not by the pixels it used to carry. Two things from §3 that are not
visible in the code:

- **13px is the floor for anything readable.** The 10–11px sizes already in the
  tree are not a precedent.
- **A bare `line-height` in new CSS is almost always the mistake** — the utility
  classes carry the size's token; the hand-picked values already in the tree are
  not a ladder to copy. Token, never a ratio.

## A field's label sits OUTSIDE its box, and that is what collides

An outlined field's floating label is `translateY(-50%)` on its own top border
(`VField.sass`), so roughly half of it — ~8px — is above the field's box. Two
things follow, and both have shipped:

- **A field needs vertical space above it.** `.v-input` carries no margin of its
  own; every pixel between two stacked fields comes from the `.v-input__details`
  row under the upper one. Anything that removes that row — `hide-details`, or a
  `hide-details="auto"` on a field that happens to have no hint — makes the two
  boxes touch, and the lower label lands on the upper border. Set `hide-details`
  on a field that stands alone or in a container with its own gap; when you set
  it on a stacked field, give the stack the spacing yourself.
- **A scroll container clips it.** In a `scrollable` dialog the scroller is
  `.v-card-text` (`VDialog.sass`), so `pt-0` there cuts the top half off the
  first field's label. The card-title above it is not padding — the clip happens
  at the scroller's own edge.

Neither shows up in vitest (happy-dom has no layout), in `vue-tsc`, or in
stylelint. `e2e/tests/layout-invariants.spec.ts` measures the rendered boxes and
is the only thing that catches them — add the screen you are building to it
rather than eyeballing the form once.

## The Chinese copy is part of the design system

Full rules, including the tables of examples and counter-examples:
[`docs/design-system.md` §8](../../docs/design-system.md#8-文案). Colours and radii
have stylelint; copy has nothing — a badly worded string ships silently. (The
*structure* around strings — keys, locales, placeholders — is gated; see the next
section. The words themselves are not.) What bites on turn one:

- **As short as it can be while the meaning stays whole; formal, neutral, plain.**
  Both failure directions are wrong: `平台检查没跑成` (too colloquial) and
  `平台检查未能顺利完成执行` (公文腔) — write `平台检查未能执行`. Second person is
  always 「你」, never 「您」, and no 「请」. Shorter never means dropping what the
  reader needs: why, what they can do next, whether their input is still there.
- **One word per concept** (§8.10): 任务 never 活; 分身 never 子 Agent; the
  project's own name for its AI teammate, never a hard-coded 芝士; the review
  flow `待审阅 → 采纳 / 退回`, never 验收 or 卡 on screen. §8.2 lists the words
  that are jargon and what replaces each — add to it when you find a new one.
- **Errors take two shapes:** `保存失败` (tried, didn't work) or `无法读取这个文件`
  (can't). Buttons are verbs that say what happens; a confirm dialog's button
  repeats the action (`移出`), not `确定`.
- **Empty states are always 「暂无 X」**, no trailing period, and nothing after
  it explaining how to make something appear there; short strings take no 句号.
  A parenthesis holds a short qualifier (`名称（英文）`), never an explanation of
  internal mechanics. Keyboard and drag hints move into `title` or go.
- **A control says its thing once**, and **deleting a UI element is riskier than
  rewording it** — if you are not certain an element is pure meta, keep it and
  raise it (§8.7 has the counter-example where the "废话" was the only signal of
  an unavailable state).

## Interface strings live in the catalog, and that part IS gated

Full rules: [`docs/i18n.md`](../../docs/i18n.md). What bites on turn one:

- **Never hardcode a user-visible string** — not in a template, not in a script,
  not as a `title` / `label` / `placeholder`. Put it in
  `frontend/src/i18n/messages/<locale>/<namespace>.json` and call `t()`. A big
  namespace is split into files whose path is the key path, `index.json` holding
  the keys of that level itself: `work.room.doc.x` lives in
  `frontend/src/i18n/messages/zh-CN/work/room/doc/index.json`. Two are flat —
  `apiError` and `roomNotice`, whose keys the backend writes out itself — so their
  directory is split by the backend module that says the sentence and the shard's
  name is not part of the key:
  `frontend/src/i18n/messages/zh-CN/apiError/usage.json` is the key `creditsMonthFull`,
  and `frontend/src/i18n/messages/zh-CN/roomNotice/usage.json` is the key
  `creditsRefused`.
- **Key names are `namespace.component.role`** (`account.signIn.submit`), never an
  English sentence or a fragment of one: a sentence-shaped key means rewording
  the Chinese forces renaming the key, and the translation goes with it.
- **Every zh-CN key ships with a non-empty English value, and every key has a
  call site.** No exception list: the translation lands in the same PR, and a key
  leaves both locales in the PR that removes its last caller.
- **Chinese that is data, not copy, is the only Chinese allowed in `src/` strings
  and templates**, and the line says so: `// i18n-data: <reason>` in `.ts` /
  `<script>`, `<!-- i18n-data: <reason> -->` in a `<template>`. Data means
  compared, parsed or stored — never text a user reads. An annotation without a
  reason fails.
- The gate is two commands, seconds each, runs anywhere: `pnpm exec vitest run
  --dir src/i18n` and `pnpm run lint:i18n` (Chinese typed into `src/` outside the
  catalog, none allowed except annotated data lines).

## The two ratchets

`frontend/stylelint-baseline.json` freezes the pre-existing violations and the
gate blocks only NEW ones — same mechanism as `tsc-baseline.json`, and for the
same reason: a rule that goes red on hundreds of existing sites gets switched off.

```bash
cd frontend
pnpm run lint:style          # check (what CI runs — read-only)
pnpm run lint:style:update   # after fixing some, ratchet the baseline DOWN
```

Palette NAMES are a **separate** gate, because stylelint parses CSS and can
therefore never see a `<template>` attribute or a `<script>` prop default. It
lives in `.claude/scripts/check-repo-rules.sh` with its own frozen baseline
(`frontend/palette-baseline.json`) and runs in `task check` and in CI's Repo
Guards:

```bash
bash .claude/scripts/check-repo-rules.sh                            # check
bash .claude/scripts/check-repo-rules.sh --update-palette-baseline  # ratchet DOWN
bash .claude/scripts/check-repo-rules.sh --self-test                # prove it still fires
```

`--update-palette-baseline` refuses to raise an entry and names the file it
refused on, and `lint:style:update` clamps every count with `Math.min` against
the baseline it already holds, so neither can be used to launder a regression in.
Do not raise a baseline to make a gate green; baselines only go down.

**Never add `--fix` to a gate script.** It rewrites the checkout and can exit 0
on a violation it silently repaired — the writing forms are `lint:style:fix` and
`lint:fix`, kept separate from the checking forms on purpose.

## 哪些检查在小机器上跑不动

`pnpm exec vitest run --dir src`、`pnpm run lint`、`pnpm run lint:style` 轻量,
到哪都能跑。`pnpm run build` 和 `pnpm run typecheck` 吃内存,能不能跑取决于这台机器有多大。

## template-comments-render-in-dev

`<template>` 里的 `<!-- ... -->` 在 vitest 和 vite dev 下保留在 DOM 里，生产构建才会去掉。
`spaces.i18n.spec.ts`、e2e 的 `english-no-cjk.spec.ts` 这类测试检查 `innerHTML` 里有没有汉字，
模板注释里的中文会让它们失败。

`:class="{ ... }"` 这类属性值里写 `// 中文` 也不安全：在 `frontend/scripts/i18n-cjk-gate.mjs`
眼里那是**模板的属性值**，算界面文案。模板里只认 `<!-- i18n-data: 理由 -->`，而花括号里放不下这种注释。

**Why:** 2026-10-03 给 26 处 `<v-btn>` 例外加 `<!-- eslint-disable-next-line ... -- 中文原因 -->`，
CI 的 frontend check 挂在 `spaces.i18n.spec.ts` 上。2026-10-12 又栽一次：`RoomMessage.vue` 的
`:class` 里写了一行 `// 改这条消息时抬掉这一行的 containment……`，CI 报
「Chinese outside the catalog」，`pnpm run lint:i18n` 复现。

**How to apply:** 模板里一律不写中文，说明写进 `<script>`（prop 的文档注释最合适）或 `<style>`／
那个 css 文件里。改完模板或模板属性后跑一次 `pnpm run lint:i18n`；批量改模板再跑
`vitest run --dir src`。

## faint-token-is-deliberate-3to1-tier

2026-10-14 #1517 查实的：impeccable 报 `--faint` 4.1–4.3:1、最深底色（`--line-2`：选中行、跑着的卡片、反馈时间线）上只有 3.37:1。但这一档不是漏改——#1328（2026-09-22）专门把元信息定成 3:1 那一档，并明确否掉了「只压深 `--faint`」：压了以后它和 `--muted` 的色阶步长会从 0.081 掉到 0.037，原话 close enough to one colour at a glance。

**Why:** 中性色阶是满的，压底下一档就会去贴上一档；#1328 自己给的方向是「新写的元信息走 `-read` 类（用 `--muted`）」。所以「把 `--faint` 调深到过 AA」是新来的人最自然会提、而这个项目已经否过的做法。

**How to apply:** 有人（包括总调度自己）要修 `--faint` 的对比度时，先看 #1328 的结论：不压令牌，把要给人读的文字换到 `--muted` / `-read`，并把「`--faint` 只用于装饰」写进设计系统说明。落点很多（上百处）就先报数，别变成全站扫荡。

## popover-inside-message-row-gets-clipped

`frontend/src/components/room/room-row.css` 的 `.im-row` 设了 `content-visibility: auto`（长对话省渲染）。它隐含 `contain: layout style paint`，而 paint containment 会把这行变成层叠上下文、并把这一行自己的溢出裁掉。任何长在行内、向上或向外弹出的绝对定位浮层都会被剪到行盒子以内——典型的受害者是 `MessageEditor` 的 `.mention-menu`（`position:absolute; bottom:100%`），屏幕上只剩贴着输入框上沿十几像素的一条细边。发消息的录入框没事，因为它在时间线外、不在 `.im-row` 里。

**Why:** 2026-10-11 查「编辑消息时 @ 候选菜单看不见」查到的。#3407 把选人菜单接进编辑框，从此踩上这个坑。症状极像「菜单没渲染」：DOM 在、rect 尺寸位置全对、`max-height` 也算得对；把 `z-index` 抬到 50 没有任何变化——不是被盖住，是被剪掉。

**How to apply:** 定位要按点测，不要查溢出：从浮层方框里每 8px 取一点跑 `document.elementsFromPoint()`，命中的全是别的元素就说明被裁或盖住。「谁在裁我」这类 overflow/contain 祖先探测查不到它——paint containment 不出现在结果里，只会列出 `.mention-menu` 自己和 `.room__scroll`。修法两条：正在编辑的那一行把 `content-visibility` 关掉（同文件里 `.cv-measure .im-row` 已有现成写法）；或把浮层 teleport 到 body，改动更大。

## volar-emit-style-for-forwarded-views

一个组件把事件声明成重载那种（`defineEmits<{ (e: 'close'): void }>()`）且多到二十来条时，Volar 解不出事件名，这个组件对外的 props 类型会退化成一条 `on*` 索引签名。它本来只是让类型变松，但只要这层被「转发视图」摊开传递（`<Inner v-bind="{ ...$props, ...$attrs }" />`），VNode 自带的 `onVnodeBeforeMount: VNodeMountHook | VNodeMountHook[]` 不是函数，撞上那条索引签名就报 `TS2345 … Property 'onVnodeBeforeMount' is incompatible with index signature`——错落在壳层文件上，看不出是内层的事件写法引起的。

**Why:** 2026-10-06 拆「其余线」36 个场景时，`AdminQueuePageView.vue` 有 25 条重载式事件，`AdminFeedbackPageView.vue` 那层壳因此报错；同批只有 4 条事件的视图没事。改成 `close: []` 这种写法后立刻干净——两个兄弟视图（`AnswerListView`、`SubmitFeedbackFormView`）本来就是这种写法，所以它们没踩到。

**How to apply:** 写或改任何 `*View.vue` 的 `defineEmits`、以及给这类视图写转发壳层时，一律用「名字 → 参数表」（`close: []`、`'update:open': [v: boolean]`），不用重载。已经写成重载、又被 `{ ...$props, ...$attrs }` 转发的，先改成这种写法，再看类型错还在不在。

## merge-main-then-pnpm-install

任务分支合了 origin/main（几天没动、主干前进几十个提交）之后，`src/views/demo/*.spec.ts` 一批文件挂在 `Failed to resolve import "@vue-flow/..."`、`@vue-flow/node-resizer/...` 上——那是主干新引入的依赖，本地 `node_modules` 还停在自己分支开出去的那一版。`frontend/` 里跑一次 `pnpm install`（锁文件已是最新，几秒完成）就好。

**Why:** 症状看着像「合错了」「vite 配置坏了」，去翻合并结果和配置全是白费。工作目录里的 node_modules 不跟着合并走。
**How to apply:** 合并主干之后、跑前端测试之前先补一次依赖。若根因不是这个，再去看具体的缺失模块。

## ratchet-red-after-merging-main

收紧判据的分支合 main 后棘轮变红，先分清两种情况，做法完全不同：

- 场景在旧基线里是 **debt** → 它本来就是 D。合完还红，说明它现在在 `ready` 里，是 main 合入期间把它搬进去的；用新判据判它 D 不算回归，照冻结场景修掉即可。
- 场景在旧基线里是 **ready** → 真回归，`--update` 不接受，只能修场景。

查法：`git show <合并前的基线提交>:frontend/scene-debt-baseline.json`，看这个路径落在 `ready` 还是 `debt`。`scene-ratchet` 只告诉你「frozen in ... because it ran standalone once」，不说它是哪天冻的。

**Why:** 2026-10-08 合 main（d140eb27）时 `views/home/Landing.vue`、`views/home/Solutions.vue` 报 A → D。它们在 main 的 #3078 里刚从 debt 搬进 ready（main 的判据不数模板里的路由标签，判它们是 A），收紧后判 D，于是成了两个新回归；换成 `components/common/NavLink.vue` 就回去了。
**How to apply:** 棘轮报回归而自己没动过那几个文件时，先查基线归属，再决定是修场景、还是回头查自己的判据。

## baseline-prose-counts-drift-on-parallel-merge

2026-10-06：#2909 与 #2821 一小时内各降了一次 `frontend/import-boundary-baseline.json`，git 把两份 JSON 的逐行增删合得很干净（66−7=59 条、54−7=47 个文件），但只有 #2909 改了复述条数的那四处文字，于是 main 上一度写着 66/54，比文件里的数大。

**Why:** 棘轮的条数在仓库里有两份：JSON 里的真数，和散文里复述的数（`.claude/rules/architecture.md`、`frontend/AGENTS.md`、`frontend/Taskfile.yml`、`docs/manual/dev/frontend.md`）。git 合得动前者，合不动后者——同一句话两个支线各改一次才会冲突，只有一支改时对方那份数字原地不动。tsc / stylelint / scene 三条基线同样有散文复述。而棘轮只拦「超出基线」（`compare()` 的 `ok: regressions.length === 0`），基线比真数大它照样绿，CI 不会替你发现。

**How to apply:** 降基线的 PR 合进 main 后，fetch 一次、从文件里重算条数和文件数，再核那四处散文；不一致就补一个只改文字的 PR。别信自己 PR 正文里写的数字，也别拿 CI 绿当这两个数一致的证据。

## render-ui-changes-before-delivering

改前端交互/版式，**交付前必须把页面真的渲染出来看**：截图，并量关键尺寸。跑测试只能证明逻辑分支对，证明不了「长什么样」。「测试全绿」不是 UI 的完成标准。

工作机装的浏览器（headless）够用：
```
npx --yes playwright@latest install chromium     # 约 114MB，装到家目录的 .cache/ms-playwright
npm i playwright-core                            # 在临时目录里；用 executablePath 指向上面那个 chrome
```
仓库自带的组件预览站（`/demo/catalog/<条目 id>`，`frontend/src/views/demo/`）能渲染**真组件、假数据**，是这条路的落点。

**Why:** 2026-10-03「提问接管输入框」我只核了逻辑（删除干净、180ms、默认首项、i18n、Esc 回路）就合入上线，测试全绿、CI 全绿；用户看到的是：提问块底部被 `overflow: hidden` 裁掉（Skip/下一步看不见）、题面被 `max-width: 640px` 限宽、窗口窄于 960px 时被 `--page-w` 又限一次（比输入框窄 98px）。三处都是「消息流卡片的样式被带进接管那一格」。他的原话：「他没有一个拉伸，然后问题都堆在一个小框里面，和 codex 差距挺大的，我觉得是 review 没做好。」——他说得对。

**How to apply:** 交互改动交付前：① 在组件预览站里把它渲染出来，和产品里**同位置的既有件**（这次是真 `RoomComposer`）摆在同一个宽度下逐档量宽度；② 至少量 480 / 700 / 900 / 1200 / 1600 五档——窄屏那一档最容易漏（媒体查询会重新限宽）；③ 量三件事：目标元素宽度是否跟同容器里的参照件一致、有没有元素被父容器裁掉（底部按钮是否可见）、有没有内容溢出。发布之后再要用户的截图，就已经是第二轮了。
