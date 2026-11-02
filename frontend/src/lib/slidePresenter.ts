// 讲者视图：放映时另开的那个窗口，和放映的那一页说同一件事。
//
// 两个窗口同源，用一条 BroadcastChannel 对话；频道名带一个随机号，写在新窗口的
// `window.name` 上——讲者视图从自己的窗口名认出该听哪一条，地址里不带任何东西，刷新那个
// 窗口也还接得上。
//
// 稿子由放映的那一边整份递过去（转好的 PDF 字节和对好页的备注）：讲者视图不自己去取，于是
// 它不需要知道文件在哪、读者有没有权限，打开得也快。
//
// 谁翻页都算：放映那边翻了告诉讲者视图，讲者视图上翻了（按钮、键盘、翻页笔）告诉放映那边，
// 两边始终停在同一页。

import type { SlideNote } from './slideNotes'

export const PRESENTER_WINDOW_PREFIX = 'cheese-presenter-'
export const PRESENTER_PATH = '/presenter'

export type PresenterDeck = {
  bytes: ArrayBuffer
  /** 已经和 PDF 对好页的备注（见 `alignSlideNotes`）；对不上就是空的。 */
  notes: SlideNote[]
  title: string
  page: number
}

export type ToPresenter = { type: 'deck'; deck: PresenterDeck } | { type: 'page'; page: number } | { type: 'end' }
export type FromPresenter = { type: 'hello' } | { type: 'go'; page: number } | { type: 'bye' }

export function presenterChannel(id: string): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null
  return new BroadcastChannel(PRESENTER_WINDOW_PREFIX + id)
}

/** 讲者视图从自己的窗口名读出频道号；不是从放映那边开出来的窗口就是 null。 */
export function presenterIdOf(name: string): string | null {
  return name.startsWith(PRESENTER_WINDOW_PREFIX) ? name.slice(PRESENTER_WINDOW_PREFIX.length) || null : null
}

export function newPresenterId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
}

/** 开讲者视图的窗口。浏览器拦了弹窗就是 null，由调用方说一句。 */
export function openPresenterWindow(id: string): Window | null {
  const width = Math.min(1280, Math.max(800, Math.round((window.screen?.availWidth ?? 1280) * 0.7)))
  const height = Math.min(820, Math.max(560, Math.round((window.screen?.availHeight ?? 800) * 0.75)))
  return window.open(PRESENTER_PATH, PRESENTER_WINDOW_PREFIX + id, `popup,width=${width},height=${height}`)
}

/** 从 `page` 往 `direction` 走一步，跳过隐藏页（放映和讲者视图都这样走）；走不动就原地。 */
export function stepShown(page: number, direction: 1 | -1, count: number, hidden: (page: number) => boolean): number {
  for (let next = page + direction; next >= 1 && next <= count; next += direction) {
    if (!hidden(next)) return next
  }
  return page
}
