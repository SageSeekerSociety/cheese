// The platform's lines in a room, rendered in the reader's language.
//
// The backend stores which sentence it said and with what (`meta.i18n.<field>`
// = `{ key, params }`, see `backend/app/domain/block/notice_text.py`) beside the
// finished Chinese text. The sentence is looked up here, at display time, so a
// language switch re-renders the lines already on screen. A line without a key
// (written before lines had one) or with a key this build does not know shows
// the stored text.
//
// An error the backend refuses with is said the same way: `error.i18n` in the
// error body is `{ key, params }` from the `apiError` catalog (see
// `app/core/errors.py`), and the API clients render it here into the error's
// `message`, falling back to the server's own sentence.

import type { Block } from '../cx_types'

import i18n from '@/i18n'

/** A stored sentence: its `roomNotice` or `apiError` key and its parameters. */
export interface NoticeMessage {
  key: string
  params?: Record<string, unknown>
}

/** The fields of an event block that can hold a platform sentence. */
export type NoticeField = 'content' | 'detail' | 'detail_label' | 'title'

function isMessage(value: unknown): value is NoticeMessage {
  if (!value || typeof value !== 'object') return false
  const key = (value as { key?: unknown }).key
  return typeof key === 'string' && /^\w+$/.test(key)
}

/** Several items said as one parameter (`listing()` in `notice_text.py`). */
interface NoticeListing {
  list: unknown[]
  quoted?: boolean
}

function isListing(value: unknown): value is NoticeListing {
  return !!value && typeof value === 'object' && Array.isArray((value as { list?: unknown }).list)
}

// Joined the way the reader's language joins a list. Chinese takes the narrow
// style: the long one ends in 「和」, which the stored sentence never had.
function joined(items: string[]): string {
  const locale = String(i18n.global.locale.value)
  return new Intl.ListFormat(locale, {
    type: 'conjunction',
    style: locale.startsWith('zh') ? 'narrow' : 'long',
  }).format(items)
}

// A parameter can itself be a sentence (a word the line chooses), a listing
// said inside the sentence, or a list of sentences, one per line — the rows of
// a detail.
function param(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => String(param(item))).join('\n')
  if (isListing(value)) {
    const items = value.list.map((item) => String(param(item)))
    return joined(value.quoted ? items.map((item) => i18n.global.t('global.listItemQuoted', { item })) : items)
  }
  return isMessage(value) ? renderNoticeMessage(value, '') : value
}

// The catalog entry a key names. The backend's two catalogs share one key space
// (`notice_text.py` refuses a key in both), so at most one of these matches.
// `zh-CN` is the complete catalog; asking it keeps a key that only lacks
// English on the fallback path of vue-i18n rather than on `fallback`.
function entryOf(key: string): string | null {
  if (i18n.global.te(`roomNotice.${key}`, 'zh-CN')) return `roomNotice.${key}`
  if (i18n.global.te(`apiError.${key}`, 'zh-CN')) return `apiError.${key}`
  return null
}

/** `message` in the reader's language, or `fallback` when it is not a sentence the catalog has. */
export function renderNoticeMessage(message: unknown, fallback: string): string {
  if (!isMessage(message)) return fallback
  const entry = entryOf(message.key)
  if (!entry) return fallback
  const params: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(message.params ?? {})) params[name] = param(value)
  const count = params.count
  return typeof count === 'number' ? i18n.global.t(entry, params, count) : i18n.global.t(entry, params)
}

/** The sentence of an API error body: its catalog key (`error.i18n`) in the
 *  reader's language, else `serverWords`, the server's own sentence. */
export function refusalText(body: unknown, serverWords: string): string {
  return renderNoticeMessage((body as { error?: { i18n?: unknown } } | null)?.error?.i18n, serverWords)
}

/** One field of an event block, in the reader's language. */
export function noticeText(block: Block, field: NoticeField = 'content'): string {
  const meta = (block.meta as Record<string, unknown> | null) ?? null
  const stored = field === 'content' ? block.content : meta?.[field]
  const fallback = typeof stored === 'string' ? stored : ''
  const keys = meta?.i18n as Record<string, unknown> | undefined
  return renderNoticeMessage(keys?.[field], fallback)
}

/** One text field of an API response, in the reader's language. A field the
 *  backend said with `say()` has its key beside it, as `i18n.<field>`. */
export function responseText(owner: object, field: string): string {
  const record = owner as Record<string, unknown>
  const fallback = typeof record[field] === 'string' ? (record[field] as string) : ''
  const keys = record.i18n as Record<string, unknown> | undefined
  return renderNoticeMessage(keys?.[field], fallback)
}
