/**
 * stylelint config.
 *
 * Beyond the property-order rule this file has always carried, it now enforces
 * the two parts of docs/design-system.md that a machine can check: no hardcoded
 * colours, and a four-step radius ladder.
 *
 * THESE RULES DO NOT GO RED ON THE EXISTING TREE. There are ~251 hardcoded hex
 * colours across 46 files, and a rule that fails on all of them on day one gets
 * switched off within the week. scripts/stylelint-ratchet.mjs freezes today's
 * count per file in stylelint-baseline.json and blocks only NEW violations —
 * the same mechanism, and the same reasoning, as tsc-baseline.json.
 *
 * Running `stylelint` directly (rather than through the ratchet) therefore
 * reports the whole backlog. That is intended: it is the paydown list.
 */

/** Kept in one place because both the rule and its message reference it. */
const RADIUS_LADDER = ['6px', '8px', '12px', '999px']

/** Every longhand, or a rule on `border-radius` alone is trivially sidestepped. */
const RADIUS_PROPERTIES = [
  'border-radius',
  'border-top-left-radius',
  'border-top-right-radius',
  'border-bottom-left-radius',
  'border-bottom-right-radius',
  'border-start-start-radius',
  'border-start-end-radius',
  'border-end-start-radius',
  'border-end-end-radius',
]

const ALLOWED_RADIUS_VALUES = [
  // The tokens are the preferred form; the raw ladder values are accepted so
  // that a file which cannot reach a CSS variable (rare, but Vuetify `style=`
  // strings exist) is not forced into a violation.
  /^var\(--radius-(sm|md|lg|pill)\)$/,
  ...RADIUS_LADDER,
  // Not design decisions: `0` removes a corner, `50%`/`9999px` make a circle,
  // and `inherit`/`initial`/`unset` defer to something else.
  '0',
  '50%',
  '100%',
  'inherit',
  'initial',
  'unset',
]

module.exports = {
  extends: ['stylelint-config-standard-scss', 'stylelint-config-standard-vue/scss', 'stylelint-prettier/recommended'],
  plugins: ['stylelint-order', 'stylelint-prettier'],
  rules: {
    /* ---- Design system: colour ---- */

    // Hex literals are the 251-site problem. A literal renders identically in
    // both themes, which means it is wrong in one of them.
    'color-no-hex': [
      true,
      {
        message:
          'Hardcoded colour. Use a design token — var(--text), var(--muted), var(--surface)… ' +
          'A literal cannot follow the dark theme. See docs/design-system.md.',
      },
    ],

    // `color: white` has exactly the same defect as `color: #fff`.
    'color-named': [
      'never',
      {
        message: 'Named colour. Use a design token (var(--surface), var(--ink)…). See docs/design-system.md.',
      },
    ],

    // Numeric rgb()/hsl() literals are the other way to hardcode a colour.
    // Deliberately NOT `function-disallowed-list: [rgba]`: the sanctioned way to
    // add alpha to a Vuetify colour is `rgba(var(--v-theme-primary), 0.12)`, and
    // banning the function outright would forbid the correct pattern along with
    // the wrong one. The regex fires only when the first argument is a NUMBER,
    // which is precisely the hardcoded case.
    'declaration-property-value-disallowed-list': [
      { '/.*/': [/(?:^|[\s,(])(?:rgba?|hsla?)\(\s*[\d.]/] },
      {
        message:
          'Hardcoded colour channels. Either use a design token, or keep the ' +
          'rgba(var(--v-theme-…), α) form so the colour still follows the theme.',
      },
    ],

    /* ---- Design system: radius ladder ---- */

    // 6 / 8 / 12 / 999 and nothing else. The tree currently holds fifteen
    // distinct radii; docs/design-system.md carries the fold-map.
    'declaration-property-value-allowed-list': [
      Object.fromEntries(RADIUS_PROPERTIES.map((property) => [property, ALLOWED_RADIUS_VALUES])),
      {
        message: `Off-ladder radius. Use var(--radius-sm|md|lg|pill) — ${RADIUS_LADDER.join(' / ')}. See docs/design-system.md.`,
      },
    ],

    'order/properties-order': [
      'display',
      'position',
      'float',
      'top',
      'right',
      'bottom',
      'left',
      'z-index',
      'width',
      'height',
      'max-width',
      'max-height',
      'min-width',
      'min-height',
      'padding',
      'padding-top',
      'padding-right',
      'padding-bottom',
      'padding-left',
      'margin',
      'margin-top',
      'margin-right',
      'margin-bottom',
      'margin-left',
      'margin-collapse',
      'margin-top-collapse',
      'margin-right-collapse',
      'margin-bottom-collapse',
      'margin-left-collapse',
      'overflow',
      'overflow-x',
      'overflow-y',
      'clip',
      'clear',
      'font',
      'font-family',
      'font-size',
      'font-smoothing',
      'osx-font-smoothing',
      'font-style',
      'font-weight',
      'line-height',
      'letter-spacing',
      'word-spacing',
      'color',
      'text-align',
      'text-decoration',
      'text-indent',
      'text-overflow',
      'text-rendering',
      'text-size-adjust',
      'text-shadow',
      'text-transform',
      'word-break',
      'word-wrap',
      'white-space',
      'vertical-align',
      'list-style',
      'list-style-type',
      'list-style-position',
      'list-style-image',
      'pointer-events',
      'cursor',
      'background',
      'background-color',
      'border',
      'border-radius',
      'content',
      'outline',
      'outline-offset',
      'opacity',
      'filter',
      'visibility',
      'size',
      'transform',
    ],
  },
  overrides: [
    {
      // src/style.css is where the tokens are DEFINED. It is the one file whose
      // job is to contain colour literals, so exempting it is not a loophole —
      // the rules above exist to push every other file through it.
      files: ['src/style.css'],
      rules: {
        'color-no-hex': null,
        'color-named': null,
        'declaration-property-value-disallowed-list': null,
        'declaration-property-value-allowed-list': null,
      },
    },
  ],
}
