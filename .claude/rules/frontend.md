---
paths:
  - "frontend/**"
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
  the current nav position, and the brand mark (§1.6). A page may have several
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
  `frontend/src/i18n/messages/<locale>/<namespace>.json` and call `t()`.
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
