import { builtinModules } from 'node:module'
import globals from 'globals'
import js from '@eslint/js'
import ts from 'typescript-eslint'
import vue from 'eslint-plugin-vue'
import prettier from 'eslint-plugin-prettier/recommended'
import vueParser from 'vue-eslint-parser'
import simpleImportSort from 'eslint-plugin-simple-import-sort'

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
      // 用户说明书的构建产物 (docs/manual → public/docs)。public/ 是 Vite 原样
      // 搬进 dist 的目录，所以文档站的 JS 会落在这个仓库里 —— 它不是我们写的
      // 代码，扫它只会得到八百条别人框架的报错。
      'public/docs/**',
      '**/dist',
      '**/eslint.config.mjs',
      'node_modules/*',
      'dist/*',
      'asset/*',
      '**/*.d.ts',
      '**/commitlint.config.ts',
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
    },
  },
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
            [`node:`, `^(${builtinModules.join('|')})(/|$)`],
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
]
