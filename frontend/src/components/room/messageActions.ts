import type { Block } from '../../cx_types'

// 一条消息上能做的事，悬停条（桌面）和长按面板（触屏）共用这几样，免得两边对
// 「复制出来的是什么」「能点哪几个表情」各有一套说法。

/** MVP 表情选择器里那八个：常用的就够了，多了是一面墙。 */
export const QUICK_EMOJIS = ['👍', '✅', '❤️', '😂', '🎉', '👀', '🙏', '➕']

// 整条消息复制成什么：芝士的回复复制 markdown 原文（代码块、列表贴到别处还是那个
// 样子）；人说的话复制屏幕上读到的字（@ 的是名字，不是 handle）。
function copyTextOf(block: Block, isAgent: boolean): string {
  if (isAgent) return block.content
  const shown = document.querySelector(`[data-mid="${block.id}"] .im-text--verbatim`)
  return shown?.textContent ?? block.content
}

/** 把整条消息放进剪贴板。浏览器不让写（没有权限、不是安全上下文）时返回 false。 */
export async function copyMessage(block: Block, isAgent: boolean): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(copyTextOf(block, isAgent))
    return true
  } catch {
    return false
  }
}
