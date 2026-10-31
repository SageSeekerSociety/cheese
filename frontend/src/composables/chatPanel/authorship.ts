// 一行该署谁的名、写什么时间、是不是我说的：时间线右边那一栏和平台通知行用的判据。
//
// 这些判据只读名册、座位和此刻在跑的那一轮，不动任何状态，所以它们和取数那半边分开：
// 几条消息一起重画时，问的是同一份名册。
import type { Ref } from 'vue'
import type { useRoomRoster } from '../../components/room/composables/useRoomRoster'
import type { Block } from '../../cx_types'
import type { Outgoing } from '../../lib/composerDrafts'
import type { NoticeAgent } from '../../lib/platformNotice'
import type { useAgentNaming } from '../useAgentNaming'

import { computed } from 'vue'

import { isAgentHandle, isPersonBlock } from '../../lib/authorship'
import { pendingMessageBlock } from '../../lib/outgoingMessage'

import i18n, { t } from '@/i18n'

/** 谁在说、什么时候说的、是不是我说的。 */
export function usePanelAuthorship(deps: {
  /** 我自己的 handle。 */
  author: string
  roster: ReturnType<typeof useRoomRoster>
  naming: ReturnType<typeof useAgentNaming>
  /** 这一轮是哪位队友（useRoomTurns）。 */
  turnAgentHandle: (turnId: string | null | undefined) => string | null | undefined
  /** 此刻连没连上：没连上时自己发的那条写的不是「发送中」。 */
  connected: Ref<boolean>
}) {
  // 某一轮那位队友。认不出是谁的轮次，房间里只坐着一位时只能是它；坐着几位时不猜：
  // 拿排在最前的那位顶上，发给 B 的那一轮出了错，提示就署成了 A。
  const severalAgents = computed(
    () => [...deps.roster.seatByHandle.value.values()].filter((row) => row.agent).length > 1
  )
  function turnAgent(turnId: string | null | undefined): NoticeAgent | null {
    const handle = deps.turnAgentHandle(turnId)
    if (handle) return { name: deps.roster.agentDisplayName(handle), handle }
    return severalAgents.value
      ? null
      : { name: deps.naming.agentName.value, handle: deps.naming.agentSeat.value?.handle ?? null }
  }

  function noticeAgent(block: Block): NoticeAgent | null {
    // 只有一位 AI 队友这次工作的结果和它交出来的东西挂在它名下（`shown_as`）：它们是
    // 那次工作的一部分。平台替它说的话、频道里发生的事都不是它说的，不署它的名。
    if (block.shown_as !== 'result' && block.shown_as !== 'card') return null
    if (isPersonBlock(block)) return null
    if (isAgentHandle(block.author) || deps.roster.seatByHandle.value.get(block.author)?.agent) {
      return { name: deps.roster.agentDisplayName(block.author), handle: block.author }
    }
    return block.turn_id ? turnAgent(block.turn_id) : null
  }
  const pendingBlock = (item: Outgoing) => pendingMessageBlock(item, deps.author)

  // 时间那一格说的是送达状态。断网时当场说等待连接；正常连接下超过阈值还没回来才说发送中；失败那行自己会说原因。
  function outgoingState(item: Outgoing): string {
    if (item.state === 'failed') return ''
    if (!deps.connected.value) return t('work.room.chat.waitingConnection')
    return item.slow ? t('work.room.chat.sending') : ''
  }

  // Every visible row asks for its time on each timeline render, and
  // toLocaleTimeString with options builds a fresh formatter per call — at a few
  // hundred rows that alone cost ~12ms a render. One formatter per language.
  let clock: { locale: string; format: Intl.DateTimeFormat } | null = null

  function fmtTime(iso: string): string {
    // Local HH:mm next to the name on the first of a run (not raw UTC). In the
    // app's language, not the browser's: a Chinese screen on an en-US system
    // otherwise reads 「07:20 PM」, which also no longer fits the gutter that a
    // continued message shows its time in.
    const locale = i18n.global.locale.value
    if (clock?.locale !== locale)
      clock = {
        locale,
        format: new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hour12: false }),
      }
    return clock.format.format(new Date(iso))
  }

  // 分栏 (2026-09-09, <@符露夀> 定): 我说的话靠右，别人和芝士靠左。
  //
  // 侧只回答一件事——**这条是不是我说的**。「谁在说」仍然由头像和名字承担，两侧
  // 都保留它们：房间里是「多个人 + 一个芝士」，左边同时坐着好几个人，光靠「在左边」
  // 分不出谁是谁。芝士也在左边，它是队友里的一个，不是对话的另一极。
  //
  // 按 handle 判，不按 author_type：author_type 只说「参与者还是平台」，而这一列
  // 里有好几个人。
  function isMine(m: Block): boolean {
    return isPersonBlock(m) && m.author === deps.author
  }

  return { severalAgents, turnAgent, noticeAgent, pendingBlock, outgoingState, fmtTime, isMine }
}
