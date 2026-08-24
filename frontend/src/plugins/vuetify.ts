/**
 * plugins/vuetify.ts
 *
 * Vuetify setup for CheeseX 知是. Theme colors mirror the design tokens in
 * src/style.css — `light` mirrors `:root`, `dark` mirrors
 * `:root[data-theme='dark']` — so hand-rolled CSS and components agree in
 * either theme. Two pipes, one palette: docs/design-system.md is the source of
 * truth for the values and for which token means what.
 *
 * THE ONE PRINCIPLE: ~95% neutral. `primary` is amber (#F57F17) but is reserved
 * for the ONE primary action button, the active nav indicator, and the brand
 * mark — never avatars, status chips, icons, or selected-row fills. Component
 * defaults below stay neutral; amber is opted into explicitly where it belongs.
 *
 * Framework documentation: https://vuetifyjs.com
 */

import '@mdi/font/css/materialdesignicons.css'

import 'vuetify/styles'

import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi'

import { resolveInitialTheme } from '@/theme'

export default createVuetify({
  theme: {
    // Which theme to boot with is resolved from the persisted preference (or
    // the OS, when the user has never chosen) — the SAME logic the inline boot
    // script in index.html already ran to stamp `<html data-theme>`. Hardcoding
    // 'light' here would leave Vuetify's components light for one frame on top
    // of an already-dark page. src/theme.ts owns the runtime switching.
    defaultTheme: resolveInitialTheme(),
    themes: {
      light: {
        colors: {
          // Accent (amber) — primary actions only.
          primary: '#F57F17',
          // Neutral surfaces / text mapped to the token ramp.
          secondary: '#6A6E76', // --muted (neutral, NOT sky blue)
          background: '#F7F8FA', // --canvas
          surface: '#FFFFFF', // --surface
          'surface-light': '#F4F5F7', // --fill
          'surface-bright': '#FFFFFF',
          'surface-variant': '#EEEFF1', // --fill-2
          'on-surface-variant': '#6A6E76',
          'page-background': '#F7F8FA',
          // Status — real status only.
          success: '#1F9D55', // --ok (also the green merge button)
          warning: '#E8901C', // --warn
          error: '#DC2626', // --danger
          info: '#6A6E76',
          // Neutral ink for text.
          'on-surface': '#36383C', // --text
          'on-background': '#36383C',
          // Brand glow. These two were REFERENCED by styles/common.scss
          // (`rgba(var(--v-theme-logo-secondary), .08)`) but never defined, and
          // because `background` is a shorthand, one invalid layer voided the
          // whole declaration — so the header's three-layer warm gradient and
          // its 12s flow animation rendered nothing at all. Defining them here
          // is what makes .header-corner-glow-flow visible.
          'logo-primary': '#F9B233',
          'logo-secondary': '#E85D2C',
        },
        variables: {
          'border-color': '#36383C',
          'border-opacity': 0.09, // ≈ --line on white
          'high-emphasis-opacity': 0.92,
          'medium-emphasis-opacity': 0.62,
          'theme-on-surface': '#36383C',
        },
      },
      // Mirrors the `:root[data-theme='dark']` block in src/style.css. When you
      // change a value there, change it here — these are the same design token
      // reaching components through a second pipe, not an independent palette.
      dark: {
        dark: true,
        colors: {
          primary: '#FFA733', // --accent (lightened: #F57F17 is 3.1:1 here)
          secondary: '#9CA2AB', // --muted
          background: '#141517', // --canvas
          surface: '#1B1D20', // --surface
          'surface-light': '#212429', // --fill
          'surface-bright': '#282C31', // --fill-2 (brighter than surface, not white)
          'surface-variant': '#282C31', // --fill-2
          'on-surface-variant': '#9CA2AB',
          'page-background': '#141517',
          success: '#3FBF7F', // --ok
          warning: '#F0A94A', // --warn
          error: '#F0625C', // --danger
          info: '#9CA2AB',
          'on-surface': '#D3D6DB', // --text
          'on-background': '#D3D6DB',
          'logo-primary': '#F9B233',
          'logo-secondary': '#E85D2C',
        },
        variables: {
          // Borders are drawn as rgba(border-color, border-opacity), so on dark
          // the colour has to flip to the light end of the ramp — reusing the
          // light theme's #36383C would paint hairlines DARKER than the surface
          // and they would vanish. 0.08 reproduces --line (#2B2E33) on
          // --surface (#1B1D20).
          'border-color': '#F3F4F6',
          'border-opacity': 0.08,
          'high-emphasis-opacity': 0.92,
          // Slightly higher than light's 0.62: the same opacity reads fainter
          // against a dark background than a light one.
          'medium-emphasis-opacity': 0.68,
          'theme-on-surface': '#D3D6DB',
        },
      },
    },
  },
  icons: {
    defaultSet: 'mdi',
    aliases,
    sets: { mdi },
  },
  // Defaults aligned to tokens: 8px-grid radii, no shouty uppercase, hairline
  // borders, NO card shadows. Shadows are reserved for menus/dialogs/drawers.
  defaults: {
    VBtn: {
      // radius 8 (controls), weight 500, no uppercase, no shadow.
      rounded: 'lg',
      flat: true,
      class: 'text-none',
      style: 'letter-spacing:0;font-weight:500;',
    },
    VCard: {
      // radius 12 (cards), border-only, no elevation.
      rounded: 'xl',
      flat: true,
      border: 'thin',
    },
    VSheet: { rounded: 'lg' },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'lg',
      hideDetails: 'auto',
      color: 'primary',
    },
    VTextarea: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'lg',
      hideDetails: 'auto',
      color: 'primary',
      autoGrow: true,
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'lg',
      hideDetails: 'auto',
      color: 'primary',
      menuProps: { rounded: 'lg' },
    },
    VAutocomplete: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'lg',
      hideDetails: 'auto',
      color: 'primary',
    },
    VCheckbox: { density: 'compact', hideDetails: 'auto', color: 'primary' },
    // Chips default neutral: 6px radius, small. Color opted in per-instance.
    VChip: { rounded: 'sm', size: 'small', label: true },
    VList: { density: 'comfortable' },
    VListItem: { rounded: 'lg' },
    VAlert: { variant: 'tonal', rounded: 'lg', border: 'start' },
    VMenu: { rounded: 'lg' },
    VDialog: { rounded: 'xl' },
    VTooltip: { location: 'top' },
    VTable: { density: 'comfortable' },
    VDataTable: { density: 'comfortable' },
    VAvatar: { rounded: 'lg' },
    VToolbar: { color: 'surface' },
  },
})
