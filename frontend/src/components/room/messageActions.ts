import type { Block } from '../../cx_types'

import { copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { REF_TOKEN } from '@/lib/refChip'

// 一条消息上能做的事，悬停条（桌面）和长按面板（触屏）共用这几样，免得两边对
// 「复制出来的是什么」「能点哪几个表情」各有一套说法。

/** MVP 表情选择器里那八个：常用的就够了，多了是一面墙。 */
export const QUICK_EMOJIS = ['👍', '✅', '❤️', '😂', '🎉', '👀', '🙏', '➕']

/** 画在消息上、却不是消息里的字：代码块角上的「复制」和「已编辑」。 */
const NOT_WORDS = '.md-code-btn, .md-pre-bar, .im-edited'

// 整条消息复制成什么：芝士的回复复制 markdown 原文（代码块、列表贴到别处还是那个
// 样子）；人说的话也是原文（换行、代码块都还在），只是引用换成屏幕上那枚标签上的字
// （@ 的是名字，不是 handle）。
function copyTextOf(block: Block, isAgent: boolean): string {
  if (isAgent) return block.content
  const shown = document.querySelector(`[data-mid="${block.id}"] .im-text`)
  return block.content.replace(REF_TOKEN, (token: string, kind?: string, id?: string, path?: string) => {
    const chip = path
      ? `[data-file="${CSS.escape(path)}"]`
      : `[data-${kind === '@' ? 'handle' : 'topic'}="${CSS.escape(id ?? '')}"]`
    return shown?.querySelector(chip)?.textContent ?? token
  })
}

/**
 * 把整条消息放进剪贴板，成功失败都弹一条 toast（走共享的复制助手，全站同一个说法）。
 * 浏览器不让写（没有权限、不是安全上下文）时返回 false，交回给要额外反馈的地方。
 */
export function copyMessage(block: Block, isAgent: boolean): Promise<boolean> {
  return copyText(copyTextOf(block, isAgent), t('work.room.message.copied'))
}

/**
 * 一条消息在屏幕上显示成的样子，给「选择文字」那一页照着再画一遍：@ 的是名字、芝士的
 * 回复带着排版。取的是页面上已经渲染、净化过的那一份，不重新渲染。代码块角上的
 * 「复制」和「已编辑」不是消息里的字，拿掉，免得选的时候一起选进去。
 * 这一条不在屏幕上时返回 null。
 */
export function shownMessageHtml(blockId: string): string | null {
  const shown = document.querySelector(`[data-mid="${blockId}"] .im-text`)
  if (!shown) return null
  const copy = shown.cloneNode(true) as HTMLElement
  copy.querySelectorAll(NOT_WORDS).forEach((el) => el.remove())
  return copy.innerHTML
}
