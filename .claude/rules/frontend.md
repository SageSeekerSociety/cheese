---
paths:
  - "frontend/**"
---

# Frontend — design tokens and the two themes

Full spec: [`docs/design-system.md`](../../docs/design-system.md). Read it before
any non-trivial styling work. This file is only the part you will otherwise get
wrong on turn one, because the tree still contains ~251 counterexamples to copy
from.

## The product has a dark theme. Every colour you write must survive it.

- **Never write a colour literal in CSS.** No `#757575`, no `rgb(...)`, no
  `hsl(...)` — use `var(--text)`, `var(--muted)`, `var(--surface)` etc. A literal
  renders the same in both themes, which means it is wrong in one of them. This
  is a lint error on new code (see the ratchet below), not a style preference.
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

## The stylelint ratchet

`frontend/stylelint-baseline.json` freezes the pre-existing violations and the
gate blocks only NEW ones — same mechanism as `tsc-baseline.json`, and for the
same reason: a rule that goes red on 251 existing sites gets switched off.

```bash
cd frontend
pnpm run lint:style          # check (what CI runs — read-only)
pnpm run lint:style:update   # after fixing some, ratchet the baseline DOWN
```

**Never add `--fix` to a gate script.** It rewrites the checkout and can exit 0
on a violation it silently repaired — the writing forms are `lint:style:fix` and
`lint:fix`, kept separate from the checking forms on purpose.

Do not raise a baseline to make a gate green. Baselines only go down.

## Sandbox reality

`pnpm run build` and full `vue-tsc` both OOM in a 2 GB agent sandbox (build also
leaves a ~10 GB core dump under `frontend/`). Do not retune
`--max-old-space-size`; state that they were not run locally and let CI cover
them. `pnpm exec vitest run --dir src`, `pnpm run lint` and `pnpm run lint:style`
all work in the sandbox and are what you should actually run.
