// The platform's lines in a room, rendered in the reader's language.
//
// The backend stores which sentence it said and with what (`meta.i18n.<field>`
// = `{ key, params }`, see `backend/app/domain/block/notice_text.py`) beside the
// finished Chinese text. The sentence is looked up here, at display time, so a
// language switch re-renders the lines already on screen. A line without a key
// (written before lines had one) or with a key this build does not know shows
// the stored text.

import type { Block } from '../cx_types'

import i18n from '@/i18n'

/** A stored sentence: its `roomNotice` key and its parameters. */
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

// A parameter can itself be a sentence (a word the line chooses), or a list of
// them, one per line — the rows of a detail.
function param(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => String(param(item))).join('\n')
  return isMessage(value) ? renderNoticeMessage(value, '') : value
}

/** `message` in the reader's language, or `fallback` when it is not a sentence the catalog has. */
export function renderNoticeMessage(message: unknown, fallback: string): string {
  if (!isMessage(message)) return fallback
  // `zh-CN` is the complete catalog; asking it keeps a key that only lacks
  // English on the fallback path of vue-i18n rather than on `fallback`.
  if (!i18n.global.te(`roomNotice.${message.key}`, 'zh-CN')) return fallback
  const params: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(message.params ?? {})) params[name] = param(value)
  const count = params.count
  return typeof count === 'number'
    ? i18n.global.t(`roomNotice.${message.key}`, params, count)
    : i18n.global.t(`roomNotice.${message.key}`, params)
}

/** One field of an event block, in the reader's language. */
export function noticeText(block: Block, field: NoticeField = 'content'): string {
  const meta = (block.meta as Record<string, unknown> | null) ?? null
  const stored = field === 'content' ? block.content : meta?.[field]
  const fallback = typeof stored === 'string' ? stored : ''
  const keys = meta?.i18n as Record<string, unknown> | undefined
  return renderNoticeMessage(keys?.[field], fallback)
}
