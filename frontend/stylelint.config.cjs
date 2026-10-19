/**
 * stylelint config.
 *
 * It enforces the one part of docs/design-system.md a machine can check and that
 * is a defect rather than a style: no hardcoded colours, because a literal cannot
 * follow the dark theme. Layout and ordering are not judged.
 *
 * The three colour rules gate with zero tolerance. Shared-config style rules
 * remain available to lint:style:fix; the gate leaves formatting and naming alone.
 */

module.exports = {
  extends: ['stylelint-config-standard-scss', 'stylelint-config-standard-vue/scss', 'stylelint-prettier/recommended'],
  plugins: ['stylelint-prettier'],
  rules: {
    /* ---- Design system: colour ---- */

    // A hex literal renders identically in
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
      },
    },
    {
      // proto-admin-variants.css is the A/B/C layout preview scaffolding, and its
      // whole job is to render the alternatives the design system rejects — variant
      // C gives cards shadows so the comparison is honest.
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
      },
    },
  ],
}
