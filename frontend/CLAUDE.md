# Frontend Conventions

Vue 3 + TypeScript + Vite. UI framework is **Vuetify 3** with `@mdi/font` icons —
not Tailwind, not Element Plus. Product is **light-theme only** (`defaultTheme: 'light'`,
deliberately not following the OS scheme).

## Design tokens are the single source of truth

`src/style.css` (`:root`) defines the neutral ramp, accent, status colors, and type
scale. `src/plugins/vuetify.ts` mirrors them into the component theme so hand-rolled
CSS and Vuetify components agree.

- **Reference the CSS variables; never hardcode a hex value.** A hardcoded color is
  invisible to a theme change later and ends up as the one element with the wrong shade.
- Reuse the existing utility classes before writing new CSS: `.t-page-title` `.t-title`
  `.t-eyebrow` `.t-body` `.t-meta` for type, `.c-*` for color, `.chip-neutral` and
  `.status-dot` for labels and status.
- Amber `#F57F17` is a rare accent — the one primary action, the active nav indicator,
  the brand mark. Not avatars, status chips, icons, or selected-row fills.

## Designing or changing anything visual

Use the **`cheese-ui`** skill. It covers the two interface modes (in-product views vs.
outward-facing landing pages, which follow opposite rules), the plan-before-code flow,
and a pre-commit checklist. This file only holds what applies to every frontend change,
including bugfixes.
