import { builtinModules } from 'node:module'
import globals from 'globals'
import js from '@eslint/js'
import ts from 'typescript-eslint'
import vue from 'eslint-plugin-vue'
import prettier from 'eslint-plugin-prettier/recommended'
import vueParser from 'vue-eslint-parser'
import simpleImportSort from 'eslint-plugin-simple-import-sort'
import a11y from 'eslint-plugin-vuejs-accessibility'

const LEGACY_NETWORK = {
  group: ['@/network', '@/network/*', '**/network/api', '**/network/api/*'],
  message:
    'The legacy network stack (src/network/) is gone. Reach the server through @/api — a domain module under src/api/, or the src/api.ts facade.',
}

export default [
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
  {
    ignores: [
      // 文档站的构建产物 (docs/site → public/docs)。public/ 是 Vite 原样
      // 搬进 dist 的目录，所以文档站的 JS 会落在这个仓库里 —— 它不是我们写的
      // 代码，扫它只会得到八百条别人框架的报错。
      'public/docs/**',
      '**/dist',
      '**/eslint.config.mjs',
      'node_modules/*',
      'dist/*',
      // 反馈原型那一版打包（`vite.feedback-proto.config.mts`）。它**不叫 dist**，
      // 所以上面那条通配管不到：产物是内联成一个文件的 7MB CSS + 500KB JS，
      // `eslint .` 会连着它一起扫，本机实测十几分钟不返回。构建产物一律不看，
      // 不管它叫什么名字。
      'dist-*/*',
      'asset/*',
      '**/*.d.ts',
      '**/stylelint.config.cjs',
    ],
  },
  js.configs.recommended,
  {
    rules: {
      'no-control-regex': 'off',
    },
  },
  ...ts.configs.recommended,
  {
    rules: {
      '@typescript-eslint/no-unused-vars': 'warn',
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-namespace': 'off',
    },
  },
  ...vue.configs['flat/recommended'],
  {
    files: ['*.vue', '**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: ts.parser,
        sourceType: 'module',
        ecmaVersion: 'latest',
        extraFileExtensions: ['.vue'],
        ecmaFeatures: { jsx: true },
      },
    },
  },
  {
    rules: {
      'vue/multi-word-component-names': 'off',
      // Explicit purposes keep password managers from treating display names as logins.
      // Non-identity text fields use "off"; numeric controls and file pickers are excluded.
      'vue/no-restricted-syntax': [
        'error',
        {
          selector:
            'VStartTag[parent.name=/^(input|textarea|v-text-field|v-textarea|v-select|v-autocomplete|v-combobox)$/]' +
            ':not(:has(VAttribute[directive=false][key.name="type"][value.value=/^(file|hidden|checkbox|radio|submit|button|reset|image|range|color|number|date|time|datetime-local|month|week)$/]))' +
            ':not(:has(VAttribute[directive=false][key.name="autocomplete"]))' +
            ':not(:has(VAttribute[directive=true][key.name.name="bind"][key.argument.name="autocomplete"]))',
          message:
            'Declare autocomplete explicitly: username, nickname, email, tel, name, current-password, new-password, or off for non-identity fields.',
        },
        {
          selector: 'VAttribute[directive=false][key.name="autocomplete"][value.value=/^(on|)$/]',
          message: 'Choose a specific autocomplete purpose or off; on leaves the field meaning ambiguous.',
        },
        {
          // docs/design-system.md §3.6. Deliberate exceptions carry an
          // eslint-disable-next-line with the reason next to them.
          selector: 'VElement[rawName="v-btn"]',
          message:
            'Use BaseButton (src/components/base/BaseButton.vue, docs/design-system.md §3.6): pick a kind and a size instead of variant/color.',
        },
        {
          // docs/design-system.md §3.12. The tree has no raw v-empty-state left
          // after the empty-state pass; a new one needs an eslint-disable-next-line
          // naming why BaseEmptyState's three sizes do not fit.
          selector: 'VElement[rawName="v-empty-state"]',
          message:
            'Use BaseEmptyState (src/components/base/BaseEmptyState.vue, docs/design-system.md §3.12): pick size page/compact/inline instead of Vuetify empty-state props.',
        },
      ],
    },
  },
  {
    // A view that declares its events as overloads
    // (`defineEmits<{ (e: 'retry'): void }>()`) loses the event names to Volar
    // once it carries twenty-odd of them: the view's props type degrades to an
    // `on*` index signature, and a forwarding shell that spreads
    // `{ ...$props, ...$attrs }` onto it then reports
    // `TS2345 … onVnodeBeforeMount is incompatible with index signature` on the
    // SHELL — a file the author of the view never touched, so the error points
    // nowhere near its cause.
    //
    // The name → parameters form (`retry: []`, `'update:open': [v: boolean]`)
    // declares the same events without the degradation, and it is what every
    // other view already uses. Scoped to views because the shell is what makes
    // it fatal: components/ has short call-signature declarations that are never
    // forwarded this way and does not need an exemption to stay put.
    files: ['src/**/*View.vue'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'CallExpression[callee.name="defineEmits"] TSCallSignatureDeclaration',
          message:
            "Declare emits as name → parameters — `retry: []`, `'update:open': [v: boolean]`. The call-signature form drops the event names, and any forwarding shell around this view then fails to type-check.",
        },
      ],
    },
  },
  {
    // `src/network/` was the second request stack: axios underneath, its own
    // envelope stripping and its own error type. Every caller moved to `@/api`
    // and the directory was deleted; this rule is what keeps it deleted. A
    // re-introduced import is how a second stack starts — and a second stack
    // means every request bug has two places to be fixed and two behaviours to
    // be compared. Never add an exemption here: if something seems to need the
    // old stack, it needs the `@/api` domain module instead.
    // docs/manual/dev/frontend.md#state-network
    //
    // Notices go through `@/lib/toast`, not vuetify-sonner's `toast`: that is
    // where a rule for every notice lives (once the page is outdated, the
    // refresh notice is the only failure it shows), and a direct import is a
    // notice the rule never sees. The next block exempts the two files that are
    // that rule, and specs, which import the mocked package to read it.
    //
    // It sits above the src/lib/docSchema block below on purpose: a later
    // matching config object replaces this rule wholesale for those files
    // rather than merging patterns, and docSchema's own `@/*` ban already
    // covers `@/network`.
    files: ['src/**/*.ts', 'src/**/*.vue'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [LEGACY_NETWORK],
          paths: [
            {
              name: 'vuetify-sonner',
              importNames: ['toast'],
              message:
                "Import `toast` from '@/lib/toast': it holds failure notices once the page is outdated (lib/apiGeneration.ts).",
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/lib/toast.ts', 'src/lib/apiGeneration.ts', 'src/**/*.spec.ts', 'src/**/*.test.ts'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [LEGACY_NETWORK] }],
    },
  },
  {
    // The document schema module is shared with code that runs outside the
    // browser bundle, so it may import packages and its own files only — never
    // the app (see src/lib/docSchema/index.ts).
    files: ['src/lib/docSchema/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { group: ['@/*', '../*'], message: 'src/lib/docSchema may not import the app; it is shared outside it.' },
            {
              group: ['vue', 'vuetify', 'vuetify/*', '@tiptap/vue-3'],
              message: 'src/lib/docSchema is framework-free.',
            },
          ],
        },
      ],
    },
  },
  {
    // `src/api.ts` is the API layer's facade: every `@/api` importer (and every
    // `vi.mock('@/api')`) resolves here, and the code lives in one module per
    // domain under src/api/. A function written here would make it a second
    // home for that code again, which is how it once reached 3747 lines.
    files: ['src/api.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'Program > :not(ExportAllDeclaration, ExportNamedDeclaration[source])',
          message:
            'src/api.ts only re-exports (`export … from`). Put the code in the src/api/<domain>.ts module it belongs to.',
        },
      ],
    },
  },
  {
    // A test reads what the code does, not what the code says (AGENTS.md, "Test
    // behaviour, never the implementation"): a spec that reads source as text
    // goes red on a refactor and stays green when behaviour breaks. The ignores
    // list only shrinks — a file leaves it when its read is replaced by a test of
    // behaviour or by a linter.
    files: ['src/**/*.spec.ts', 'src/**/*.test.ts'],
    ignores: [
      'src/branchProtectionSection.spec.ts',
      'src/offScreenSkipping.spec.ts',
      'src/styles/docBlocks.spec.ts',
      'src/views/home/Landing.spec.ts',
      'src/vShowDisplayUtilities.spec.ts',
      'src/i18n/catalog.spec.ts',
      'src/components/base/BaseButton.spec.ts',
      'src/components/base/SettingsRow.spec.ts',
      'src/components/CheeseAvatar.spec.ts',
      'src/views/feedback/scroll.spec.ts',
      'src/views/demo/demoTokens.spec.ts',
      'src/components/ChatPanel.mentionMenu.spec.ts',
      'src/views/demo/demoTransitions.spec.ts',
      'src/views/demo/DemoTokensPage.spec.ts',
      'src/components/admin/AdminGrid.spec.ts',
      'src/components/common/Navigation/BottomAppBar.spec.ts',
    ],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'ImportDeclaration[source.value=/\\?raw$/]',
          message: "A test reads behaviour, not source; see AGENTS.md 'Test behaviour, never the implementation'.",
        },
        {
          selector: 'ImportDeclaration[source.value=/^(node:)?fs(\\u002Fpromises)?$/]',
          message: "A test reads behaviour, not source; see AGENTS.md 'Test behaviour, never the implementation'.",
        },
      ],
    },
  },
  // Layout (prettier, import order) is judged like any other rule: CI's
  // `pnpm run lint` fails a tree that does not hold it, and the pre-commit
  // `frontend-fix` hook applies it to the staged files with `eslint --fix`.
  prettier,
  {
    rules: {
      'prettier/prettier': [
        'error',
        {},
        {
          usePrettierrc: true,
        },
      ],
    },
  },
  {
    plugins: {
      'simple-import-sort': simpleImportSort,
    },
    rules: {
      'simple-import-sort/imports': [
        'error',
        {
          groups: [
            // Bare builtin names only. Node 23.5+ also lists prefix-only modules
            // (`node:test`, `node:sqlite`, ...) in builtinModules; left in, they
            // match this longer pattern instead of `node:` and sort after every
            // other `node:` import, so the same file passes on one Node and fails
            // on another.
            [`node:`, `^(${builtinModules.filter((m) => !m.startsWith('node:')).join('|')})(/|$)`],
            // style less,scss,css
            ['^.+\\.less$', '^.+\\.s?css$'],
            // Side effect imports.
            ['^\\u0000'],
            ['^@?\\w.*\\u0000$', '^[^.].*\\u0000$', '^\\..*\\u0000$'],
            ['^vue', '^@vue', '^@?\\w', '^\\u0000'],
            ['^@/utils'],
            ['^@/composables'],
            // Parent imports. Put `..` last.
            ['^\\.\\.(?!/?$)', '^\\.\\./?$'],
            // Other relative imports. Put same-folder imports and `.` last.
            ['^\\./(?=.*/)(?!/?$)', '^\\.(?!/?$)', '^\\./?$'],
          ],
        },
      ],
      'simple-import-sort/exports': 'error',
    },
  },
  // The accessibility rules (eslint-plugin-vuejs-accessibility), as plain
  // errors. A line that departs from one carries an eslint-disable whose
  // reason names the keyboard path or why the rule does not apply there.
  ...a11y.configs['flat/recommended'],
  {
    rules: {
      // HTML associates a label with its control either by wrapping it or by
      // `for`; either one gives the control its name. The plugin's default
      // demands both, which no label pointing `for` at a Vuetify field can meet,
      // since the field's <input> is not a child of the label.
      'vuejs-accessibility/label-has-for': ['error', { required: { some: ['nesting', 'id'] } }],
    },
  },
]
