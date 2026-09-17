import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import en from './messages/en'
import zhCN from './messages/zh-CN'
import untranslatedDebt from './untranslated.json'
import unusedDebt from './unused.json'

// The two catalog directories must describe the same product. A namespace that
// exists in one locale and not the other is how PR #929 shipped: an English
// visitor reached the workspace and got a silently half-Chinese screen, and
// nothing in the build said so. These tests are that "something".
//
// Two committed debt lists make the remaining gaps explicit rather than silent:
//   untranslated.json — leaves that render but have no English
//   unused.json       — leaves no source file references
// They are disjoint: a leaf nobody renders is not a translation backlog, and
// counting it as one would inflate how much work is actually left.
// Both may only be edited deliberately: an entry that no longer describes
// reality fails the suite, so the lists shrink honestly instead of rotting.

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
const CJK = /[㐀-䶿一-鿿豈-﫿]/
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
const stripComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/gm, '$1')

const sourceText = new Map(
  sourceFiles(SRC)
    .filter((p) => !relative(SRC, p).startsWith('i18n/messages/'))
    .map((p) => [relative(SRC, p), stripComments(readFileSync(p, 'utf8'))])
)

const referenced = new Set<string>()
const calledLiterally = new Set<string>()
for (const text of sourceText.values()) {
  for (const m of text.matchAll(reference)) referenced.add(`${m[1]}.${m[2]}`)
  for (const m of text.matchAll(callSite)) calledLiterally.add(`${m[1]}.${m[2]}`)
}

describe('locale catalogs', () => {
  it('has a zh-CN and an en entry for every key, or an explicit debt entry', () => {
    const untranslated = new Set(untranslatedDebt)
    const unused = new Set(unusedDebt)
    const missing = zhIds.filter((id) => !enById.has(id) && !untranslated.has(id) && !unused.has(id))
    expect(missing, `${missing.length} 个键既没有英文也不在任何一份清单里`).toEqual([])
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

  it('keeps untranslated.json honest — no stale or dangling entries', () => {
    expect(
      untranslatedDebt.filter((id) => enById.has(id)),
      '这些键已经有英文了，请从 untranslated.json 删掉'
    ).toEqual([])
    expect(
      untranslatedDebt.filter((id) => !zhById.has(id)),
      '这些键在 zh-CN 里都不存在，是悬空条目——列出叶子，不要列子树'
    ).toEqual([])
    expect(
      untranslatedDebt.filter((id) => unusedDebt.includes(id)),
      '这些键也躺在 unused.json 里：没人渲染的东西不算翻译欠账，请从 untranslated.json 删掉'
    ).toEqual([])
  })

  it('reports how much is still untranslated', () => {
    // Not an assertion about the size — just make the number visible in the run
    // output, so "we translated everything" is checkable at a glance.
    console.log(`[i18n] untranslated: ${untranslatedDebt.length} / ${zhIds.length} keys`)
    expect(untranslatedDebt.length).toBeLessThanOrEqual(zhIds.length)
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

describe('source and catalog agree', () => {
  it('resolves every key the source calls by name', () => {
    const unknown = [...calledLiterally].filter((id) => !zhById.has(id)).sort()
    expect(unknown, '源码里调用了 catalog 中不存在的键（或调用了子树而非叶子）').toEqual([])
  })

  it('does not carry keys no source file uses, unless listed in unused.json', () => {
    const debt = new Set(unusedDebt)
    const unused = zhIds.filter((id) => !referenced.has(id) && !debt.has(id)).sort()
    expect(unused, '这些键没有任何文件引用，要么接上要么删掉，或加进 unused.json').toEqual([])
  })

  it('keeps unused.json honest — every entry is genuinely unreferenced', () => {
    const revived = unusedDebt.filter((id) => referenced.has(id)).sort()
    expect(revived, '这些键已经有人用了，请从 unused.json 删掉').toEqual([])
    const dangling = unusedDebt.filter((id) => !zhById.has(id)).sort()
    expect(dangling, '这些键在 zh-CN 里都不存在，是悬空条目——列出叶子，不要列子树').toEqual([])
  })
})
