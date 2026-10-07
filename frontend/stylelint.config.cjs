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

/** The four viewport tiers, as compiled CSS values. Source: src/styles/breakpoints.scss. */
const BREAKPOINT_TIERS = ['767.98px', '959.98px', '1179.98px', '1279.98px']

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

    /* ---- Design system: viewport breakpoints ---- */

    // The four tiers are SCSS constants in src/styles/breakpoints.scss, because a
    // media query cannot read a CSS variable. A file that can reach them writes
    // `@include bp.below(bp.$bp-phone)`; a plain-CSS file has no choice but to
    // write 767.98px. Both compile down to a value in this list, so the gate
    // polices the VALUES and leaves the spelling to the ordinary rules.
    //
    // The feature name has to be spelled per form: `(max-width: 600px)`,
    // `(min-width: 1280px)` and the range form `(width < 600px)` are three
    // different names to stylelint, and all three are in use in this tree.
    'media-feature-name-value-allowed-list': [
      {
        'min-width': BREAKPOINT_TIERS,
        'max-width': BREAKPOINT_TIERS,
        width: BREAKPOINT_TIERS,
      },
      {
        message:
          `Off-tier viewport breakpoint. The four tiers are ${BREAKPOINT_TIERS.join(' / ')} — ` +
          'fold the value, use the mixin, or record it as an exception in docs/breakpoint-inventory.md.',
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
    {
      // The docs site is a separate app with its own layout ladder (1680 / 1239 /
      // 1099 / 820 / 760 / 560 …); its one stylesheet is linted here for colours
      // and radii, but the product's four viewport tiers do not govern it.
      files: ['../docs/site/src/style.css'],
      rules: {
        'media-feature-name-value-allowed-list': null,
      },
    },
    {
      // breakpoints.scss is where the tiers are DEFINED, so it is the one file
      // that must write `@media (max-width: $width)` — a variable, not a tier.
      // Exempting the definition site is the same call as exempting src/style.css
      // from the colour rules.
      files: ['src/styles/breakpoints.scss'],
      rules: {
        'media-feature-name-value-allowed-list': null,
      },
    },
    {
      // proto-admin-variants.css is the A/B/C layout preview scaffolding, and its
      // whole job is to render the alternatives the design system rejects — variant
      // C gives cards shadows and an off-ladder radius so the comparison is honest.
      // It is only ever loaded by the preview build (`?v=a|b|c`), never by the app
      // bundle, so the rules above do not have a say over it. The tag in the corner
      // of every screenshot is also off-theme on purpose: it must stay readable over
      // both light and dark pages. Named as one file, not a glob — the next proto
      // stylesheet should have to make this decision again.
      files: ['src/proto-admin-variants.css'],
      rules: {
        'color-no-hex': null,
        'color-named': null,
        'declaration-property-value-disallowed-list': null,
        'declaration-property-value-allowed-list': null,
      },
    },
  ],
}
