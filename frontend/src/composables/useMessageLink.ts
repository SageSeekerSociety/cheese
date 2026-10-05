// 一条消息在站内的地址，和「复制这条链接」。
//
// 这是组件要的第三类东西（组件不能 import vue-router，见 useNavigation.ts）：
// 消息的悬停条和手机上那张操作面板都要这一条，所以它落在这里，两处共用一套说法。
//
// 地址形状沿用搜索那一页的（`views/workspace/search/messages.palette.ts`）：进这个
// 话题、落在这一条上——`?block=<id>`；这一条是某张活卡里的对话，就再多带 `tab` 和
// `card`，卡片里的对话不在房间的对话里（那条路已经发出去过，不能改）。
import type { Block } from '../cx_types'

import { useNavigation } from './useNavigation'

import { copyText } from '@/commands/copy'
import { t } from '@/i18n'

export function useMessageLink() {
  // 宿主没装路由就是 null：演示页和一整类单测里没有路由，那时没有链接可给，
  // 调用方据此不画这一颗按钮。
  const nav = useNavigation()

  /** 这一条在站内的完整地址。拿不到路由、或者此刻不在某个话题里 → `null`。 */
  function hrefOf(block: Block): string | null {
    const params = nav?.route?.params
    const projectId = params?.projectId
    const topicId = params?.topicId
    const taskId = params?.taskId
    if (!nav || typeof projectId !== 'string' || typeof topicId !== 'string') return null
    const path = nav.href(
      typeof taskId === 'string'
        ? { name: 'workspace-task', params: { projectId, topicId, taskId }, query: { block: block.id } }
        : { name: 'workspace-topic', params: { projectId, topicId }, query: { block: block.id } }
    )
    // `href` gives a router path; the thing a person pastes needs the origin on it.
    return path ? new URL(path, window.location.origin).href : null
  }

  /** 复制这一条的链接，成功失败都弹一条 toast（走共享的复制助手）。 */
  async function copy(block: Block): Promise<boolean> {
    const href = hrefOf(block)
    if (!href) return false
    return copyText(href, t('work.room.message.linkCopied'))
  }

  return { hrefOf, copy }
}
