---
paths:
  - "frontend/**"
---

# Frontend — design tokens and the two themes

Full spec: [`docs/design-system.md`](../../docs/design-system.md). Read it before
any non-trivial styling work. This file is only the part you will otherwise get
wrong on turn one.

The gates pass on a tree that still holds violations: the existing ones are
frozen in a baseline and only NEW ones are blocked. So a green gate does not
mean the file open in front of you is clean — it may well be one of the frozen
ones. `frontend/stylelint-baseline.json` and `frontend/palette-baseline.json`
say which files have an allowance; check there before copying a neighbour.

After a change, `frontend-design` (a skill) walks the whole checklist including
the parts no gate can see.

## The product has a dark theme. Every colour you write must survive it.

- **Never write a colour literal in CSS.** No `#757575`, no `rgb(...)`, no
  `hsl(...)` — use `var(--text)`, `var(--muted)`, `var(--surface)` etc. A literal
  renders the same in both themes, which means it is wrong in one of them. This
  is a lint error on new code (see the ratchet below), not a style preference.
- **A Vuetify palette NAME is a colour literal too, and this is the one that
  actually shipped broken.** `color="grey-lighten-5"`, `class="bg-grey-lighten-5"`,
  `color="white"`, `class="text-grey-darken-1"` — these have no `#`, they read
  like semantic colours, and `grey-lighten-5` is exactly `#FAFAFA` in *both*
  themes, forever. Seven of them sat on the global shell (rail, title bar,
  mobile bars, `v-main`), so when the dark theme landed the shell stayed
  near-white while the text on it followed `on-surface` and went pale grey:
  unreadable, on every page. Use `background` / `surface` / `surface-light` /
  `surface-bright` / `on-surface-variant` / `primary` instead — including in
  `withDefaults` prop defaults, which is how `SecondaryNavigation` handed grey to
  every page that used it. Table of replacements: `docs/design-system.md` §1.2.
  - Exception, and write the reason in the code when you take it: where the
    BACKGROUND is itself theme-invariant, the ink on it must be too. The default
    avatar's background is the `#rrggbb` `avatarColor()` computes at a fixed
    OKLCH lightness (L = 0.54 / C = 0.12) — the same value in both themes, so
    its `#fff` text is correct and "fixing" it to a token breaks it.
- The neutral ramp is `--ink` (titles) > `--text` (body) > `--muted` (secondary)
  > `--faint` (meta), on `--surface` (cards) over `--canvas` (app bg), separated
  by `--line`. Pick by how important the information is, not by how it looks.
- **Status colours come in threes and are not interchangeable**: `--danger` is
  the MARK (dot, border, icon), `--danger-ink` is the TEXT, `--danger-wash` is
  the BACKGROUND. Using the mark colour for text is the single most common
  defect — in light theme `--warn` measures 2.34:1 on white, well under the
  4.5:1 needed to read. Same shape for `--ok` and `--accent`.
- Amber (`--accent`) is reserved for the ONE primary action, the active nav
  indicator, and the brand mark. Not avatars, not status chips, not ordinary
  icons. ~95% of any screen is neutral.
- Cards get a `--line` border and **no shadow**. `--shadow-1`/`--shadow-2` are
  for menus, dialogs and drawers only — things floating above the page.

If you find yourself writing `:root[data-theme='dark'] .thing { ... }`, stop:
nine times out of ten the real fix is that `.thing` picked the wrong token.

## Motion: still is the default

Full rules: [`docs/design-system.md` §9](../../docs/design-system.md#9-动效). The three
that get written wrong on turn one:

- **`:hover` changes colour, never position.** This side of the product is dense
  lists — a sidebar of dozens of topics, a chat of dozens of messages, a board
  column of a dozen cards. Lift each row 2px under the pointer and what a person
  sees is the column jumping, not which row they are on; the background change
  already said that. (The community half does lift, in ~33 files. It is the side
  that has to come off it, not this one.)
- **Never `transition: all`** — it drags `width`/`height`/`padding` along, so the
  browser relayouts every frame inside a list, and nobody can tell what the line
  was meant to animate. Name the properties.
- **Anything `infinite` needs its own reduced-motion escape.** The global
  fallback in `style.css` squeezes durations to `0.001ms`, which turns a 1.6s
  pulse into a strobe — worse than leaving it. Write
  `@media (prefers-reduced-motion: reduce) { animation: none }` next to it, and
  make sure the thing still says what it meant with the animation off.

Durations are 0.12s (answering the pointer) / 0.2s (appearing, disappearing) /
0.3s (a whole panel moving in or out). Easing is `ease`; `ease-in-out` for loops;
`linear` only for genuinely constant motion.

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

- Radius: `--radius-sm` 6 / `--radius-md` 8 / `--radius-lg` 12 / `--radius-pill`
  999. Nothing else. (The tree currently holds 15 distinct values.)
- Font size: prefer the `.t-page-title` / `.t-title` / `.t-body` / `.t-eyebrow` /
  `.t-meta` utility classes. Hand-written sizes are limited to 12/13/14/15/18/23
  px, and **13px is the floor for anything readable** — there are 29 sites at
  10–11px and they are not a precedent to follow.
- Spacing: 8px grid (4/8/12/16/24/32). Prefer Vuetify's `pa-*`/`ma-*` utilities.

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

Full rules: [`docs/design-system.md` §8](../../docs/design-system.md#8-文案). Colours
and radii have stylelint; copy has nothing — a badly worded string ships silently.
(The *structure* around strings — keys, locales, placeholders — is gated; see the
next section. The words themselves are not.) So the one thing to internalise
before you type user-facing Chinese:

- **正式、清晰、自然、简明.** Both failure directions are wrong: `平台检查没跑成`
  (too colloquial) and `平台检查未能顺利完成执行` (公文腔) — write
  `平台检查未能执行`. Second person is always 「你」, never 「您」.
- **No implementation words on screen.** 跑沙箱 / 干活 → 运行任务; system prompt /
  注入 → 角色设定; 算力节点 / 连接器 → 设备; 小队 → 团队; 一页纸总结 → 概要;
  知是基座 → 默认镜像. Test: *would someone opening this product for the first
  time understand the word?* If not, it is jargon. §8.2 carries the running list
  — add to it when you find a new one.
- **Empty states are always 「暂无 X」**, no trailing period. Short strings
  (labels, buttons, empty states, single-sentence hints) take no 句号 at all.
- **Parentheses never explain internal mechanics.** `理由（会留在卡上）` → `理由`.
  A parenthesis may hold a short qualifier (`名称（英文）`), not a sentence.
- **Keyboard/drag hints do not live on screen** — move them into `title`, or
  delete them. A placeholder counts as on-screen.
- **A control says its thing once.** When a button is right there, the adjacent
  text states the STATE (`暂无关联账号`), not the action again.
- **Deleting a UI element is riskier than rewording it.** If you are not certain
  an element is pure meta, keep it and raise it — see the §8.7 counter-example
  where the "废话" was also the only signal of an unavailable state.

## Interface strings live in the catalog, and that part IS gated

Full rules: [`docs/i18n.md`](../../docs/i18n.md). The parts you will otherwise get
wrong on turn one:

- **Never hardcode a user-visible string** — not in a template, not in a script,
  not as a `title` / `label` / `placeholder`, not in text the code assembles. Put
  it in `frontend/src/i18n/messages/<locale>/<namespace>.json` and call `t()`.
- **Key names are `namespace.component.role`** (`account.signIn.submit`), never an
  English sentence and never a sentence fragment. A sentence-shaped key means
  rewording the Chinese forces renaming the key, which throws the translation away.
- **Never create an empty English namespace to satisfy a check.** That turns
  "missing" into "present but blank", which is exactly the silent state the gates
  exist to prevent. Either write the translation, or leave the keys in
  `frontend/src/i18n/untranslated.json`.
- **Never edit `untranslated.json` / `unused.json` just to get green.**
  `catalog.spec.ts` fails on entries that no longer describe reality (already
  translated, already referenced, or dangling), so the lists can only shrink
  honestly.
- `pnpm exec vitest run --dir src/i18n` is the whole i18n gate — seconds, runs anywhere.

## The two ratchets

`frontend/stylelint-baseline.json` freezes the pre-existing violations and the
gate blocks only NEW ones — same mechanism as `tsc-baseline.json`, and for the
same reason: a rule that goes red on 251 existing sites gets switched off.

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
refused on, so "just regenerate the baseline" is not the escape from a red gate.

**Never add `--fix` to a gate script.** It rewrites the checkout and can exit 0
on a violation it silently repaired — the writing forms are `lint:style:fix` and
`lint:fix`, kept separate from the checking forms on purpose.

Do not raise a baseline to make a gate green. Baselines only go down.

## 哪些检查在小机器上跑不动

`pnpm exec vitest run --dir src`、`pnpm run lint`、`pnpm run lint:style` 轻量,
到哪都能跑,是基线组合(前两者也已接进 `.claude/scripts/check.sh`)。

`pnpm run build` 和 `pnpm run typecheck` 吃内存,能不能跑取决于这台机器有多大——
**先试一次再下结论**,别预先宣布跑不了。被 OOM 杀掉时怎么办(不要调
`--max-old-space-size`、要明说没跑成、记得删 core dump)是平台层的事,写在
`backend/sandbox/skills/cheese/SKILL.md` 里,对每个被托管的仓库都一样。
