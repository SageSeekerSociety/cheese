/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from './messages/en'
import zhCN from './messages/zh-CN'

// The two catalog directories must describe the same product. A namespace that
// exists in one locale and not the other is how PR #929 shipped: an English
// visitor reached the workspace and got a silently half-Chinese screen, and
// nothing in the build said so. These tests are that "something".
//
// There is no exception list. Every zh-CN leaf has a non-empty English value,
// and every leaf has a call site; a key that cannot meet both is translated or
// deleted, never parked.

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// vue-i18n nests freely, and the catalog uses that: `global.formValidation` is a
// subtree of five messages, not one message. Only leaves are messages. Flatten
// to them before comparing anything — a two-level walk would treat the whole
// subtree as a single opaque value, and every per-message check below would then
// pass vacuously over it.
type Messages = { [key: string]: string | Messages }
type Catalog = Record<string, Messages>

function leaves(catalog: Messages, prefix = ''): { id: string; value: string }[] {
  return Object.entries(catalog).flatMap(([key, value]) => {
    const id = prefix ? `${prefix}.${key}` : key
    return typeof value === 'string' ? [{ id, value }] : leaves(value, id)
  })
}

const zhEntries = leaves(zhCN as Catalog)
const enEntries = leaves(en as Catalog)
const zhIds = zhEntries.map((e) => e.id)
const enById = new Map(enEntries.map((e) => [e.id, e.value]))
const zhById = new Map(zhEntries.map((e) => [e.id, e.value]))

// Language names and the switch labels are endonyms: the same string in every
// locale, so a visitor who cannot read the current language can still find the
// one they want. They are not translations, so they do not live in these
// catalogs at all — they are constants in `languages.ts`. That leaves no key for
// which a CJK value is correct in the English catalog, and this scan has no
// exemption to hide behind.
// Escapes, not literals: NFC turns a literal U+F900 into U+8C48, which widens
// the last range to Hangul and to the surrogate halves of every emoji.
const CJK = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/
const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(vue|ts)$/.test(entry.name) ? [path] : []
  })
}

// Every namespace name currently in use. Matching on these (rather than on any
// `foo.bar` in the source) keeps unrelated object access out of the reference
// scan below. The rest of the path is captured whole so that a nested call like
// `t('global.formValidation.required')` is attributed to that leaf and not to
// the `global.formValidation` subtree above it.
const NS = Object.keys(zhCN as Catalog).join('|')
const PATH = '([\\w.]+)'
const reference = new RegExp(`\\b(${NS})\\.${PATH}`, 'g')
const callSite = new RegExp(`(?:\\$?t|te|tm)\\(\\s*['"\`](${NS})\\.${PATH}['"\`]`, 'g')

// A key named in a comment is not a call site, and a commented-out call does not
// show the user a raw key. Strip comments before looking for references, so this
// scan reports what the running code actually asks for.
const stripJsComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\/|(^|[^:])\/\/[^\n]*/gm, '$1')
// A .vue template has no JS comments: `accept="image/*"` there is an attribute, and
// treating its `/*` as a comment opener would blank everything up to the next `*/`
// in the script block — the keys in between would read as unused. So in a .vue
// file only the <script> and <style> blocks get JS comment stripping.
const stripComments = (path: string, text: string) =>
  path.endsWith('.vue')
    ? text.replace(
        /(<(script|style)\b[^>]*>)([\s\S]*?)(<\/\2>)/g,
        (_m, open: string, _tag: string, body: string, close: string) => open + stripJsComments(body) + close
      )
    : stripJsComments(text)

const sourceText = new Map(
  sourceFiles(SRC)
    .filter((p) => !relative(SRC, p).startsWith('i18n/messages/'))
    .map((p) => [relative(SRC, p), stripComments(p, readFileSync(p, 'utf8'))])
)

// A key built at run time — t(`spaces.members.role.${role}`) — names a family of
// leaves, not one. Each `${…}` stands for one path segment; the leaves it can
// reach count as referenced, and the family must not be empty (deleting the
// subtree because no literal call names its leaves shows the user raw keys).
const dynamicSite = new RegExp(`(?:\\$?t|te|tm)\\(\\s*\`((?:${NS})\\.[^\`]*\\$\\{[^\`]*)\``, 'g')
const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
function dynamicPattern(template: string): RegExp {
  const parts = template.split(/\$\{[^}]*\}/)
  return new RegExp(`^${parts.map(escapeRegExp).join('[^.]+')}$`)
}

const referenced = new Set<string>()
const calledLiterally = new Set<string>()
const dynamicTemplates = new Set<string>()
for (const text of sourceText.values()) {
  for (const m of text.matchAll(reference)) referenced.add(`${m[1]}.${m[2]}`)
  for (const m of text.matchAll(callSite)) calledLiterally.add(`${m[1]}.${m[2]}`)
  for (const m of text.matchAll(dynamicSite)) dynamicTemplates.add(m[1])
}
for (const template of dynamicTemplates) {
  const pattern = dynamicPattern(template)
  for (const id of zhIds) if (pattern.test(id)) referenced.add(id)
}

describe('locale catalogs', () => {
  it('has a non-empty English value for every zh-CN key', () => {
    const missing = zhIds.filter((id) => !enById.get(id)?.trim())
    expect(
      missing,
      `${missing.length} 个键没有英文（或英文是空串）：在 messages/en/<命名空间>.json 里写上译文`
    ).toEqual([])
  })

  it('has no English key that zh-CN lacks', () => {
    // The other direction. A key only en knows about can never render, because
    // `fallbackLocale` is zh-CN and every call site is written against the
    // Chinese catalog.
    const orphans = enEntries
      .filter((e) => !zhById.has(e.id))
      .map((e) => e.id)
      .sort()
    expect(orphans, '这些键只在 en 里存在，永远不会被渲染').toEqual([])
  })

  it('uses the same placeholders in both locales', () => {
    const mismatched = zhEntries
      .filter((e) => enById.has(e.id))
      .map((e) => ({
        id: e.id,
        zh: placeholders(e.value),
        en: placeholders(enById.get(e.id) as string),
      }))
      .filter(({ zh, en: enTokens }) => zh.join(',') !== enTokens.join(','))
    expect(mismatched, '占位符不一致会让译文渲染出 undefined 或缺字').toEqual([])
  })

  it('never ships Chinese text as the English translation', () => {
    const withCjk = enEntries.filter((e) => CJK.test(e.value)).map((e) => e.id)
    expect(withCjk, '英文词条里出现了中日韩字符——多半是复制中文凑数').toEqual([])
  })
})

// vue-i18n parses every message, and a syntax error in one — a bare `@`, its
// linked-message marker — only logs in development, where the raw text still
// renders. The production build throws while rendering, the app's error handler
// swallows it, and the page comes up blank: /solutions shipped that way over
// `ops@okcheese.com`. Write a literal `@` as {'@'}.
describe('every message compiles', () => {
  it.each([
    ['zh-CN', zhCN],
    ['en', en],
  ])('%s', (locale, catalog) => {
    const errors: string[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((message: unknown) => {
      errors.push(String(message).split('\n')[0])
    })
    const i18n = createI18n({
      legacy: false,
      locale,
      messages: { [locale]: catalog },
      missingWarn: false,
      fallbackWarn: false,
      warnHtmlMessage: false,
    })
    const broken = leaves(catalog as Messages).filter(({ id }) => {
      errors.length = 0
      i18n.global.t(id)
      return errors.some((e) => e.startsWith('Message compilation error'))
    })
    spy.mockRestore()
    expect(broken.map(({ id, value }) => `${id}: ${value}`)).toEqual([])
  })
})

describe('source and catalog agree', () => {
  it('does not start a block comment inside a line comment', () => {
    const source = "// The response has image/* content.\nt('tasks.preview.unavailable')\n/* style */"
    expect(stripComments('Example.ts', source)).toContain("t('tasks.preview.unavailable')")
  })

  it('does not read an attribute in a .vue template as a block comment', () => {
    const source = [
      '<template><input accept="image/*" :label="t(\'tasks.preview.unavailable\')" /></template>',
      '<script setup lang="ts">',
      '/** docs */',
      "// t('tasks.preview.appUnavailable')",
      '</script>',
    ].join('\n')
    const stripped = stripComments('Example.vue', source)
    expect(stripped).toContain("t('tasks.preview.unavailable')")
    expect(stripped).not.toContain("t('tasks.preview.appUnavailable')")
  })

  it('resolves every key the source calls by name', () => {
    const unknown = [...calledLiterally].filter((id) => !zhById.has(id)).sort()
    expect(unknown, '源码里调用了 catalog 中不存在的键（或调用了子树而非叶子）').toEqual([])
  })

  it('resolves every key family the source builds at run time', () => {
    const empty = [...dynamicTemplates].filter((template) => !zhIds.some((id) => dynamicPattern(template).test(id)))
    expect(empty.sort(), '源码在运行时拼出的键，catalog 里一个对得上的都没有').toEqual([])
  })

  it('does not carry keys no source file uses', () => {
    const unused = zhIds.filter((id) => !referenced.has(id)).sort()
    expect(unused, '这些键没有任何文件引用：接上调用，或者从 zh-CN 和 en 两边删掉').toEqual([])
  })
})
