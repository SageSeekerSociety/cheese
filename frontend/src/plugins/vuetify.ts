/**
 * plugins/vuetify.ts
 *
 * Vuetify setup for CheeseX 知是. Theme colors mirror the design tokens in
 * src/style.css (:root) so hand-rolled CSS and components agree.
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

export default createVuetify({
  theme: {
    // Light-only product — force light regardless of OS scheme.
    defaultTheme: 'light',
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
        },
        variables: {
          'border-color': '#36383C',
          'border-opacity': 0.09, // ≈ --line on white
          'high-emphasis-opacity': 0.92,
          'medium-emphasis-opacity': 0.62,
          'theme-on-surface': '#36383C',
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
