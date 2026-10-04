// 消息上的表情回应（Slack 的语义）：一次只开一条消息的选择条；点了一个表情先在本地
// 切换自己那一票（lib/reactions），再用接口返回的汇总写回。后端随后广播的 `reaction`
// 帧和这次本地写回是幂等的。
import type { Ref } from 'vue'
import type { Block, ReactionAgg } from '../cx_types'

import { ref } from 'vue'

import { toggleReaction as apiToggleReaction } from '../api'
import { t } from '../i18n'
import { reactOptimistically } from '../lib/reactions'

export interface MessageReactionsDeps {
  find: (blockId: string) => Block | undefined
  errorMsg: Ref<string | null>
  /** 开房间那次取历史还没落地时，帧里来的汇总先记在这里，等快照合并时套上去。 */
  pendingHistory: () => Map<string, ReactionAgg[]> | null
  /** 我是谁：先在本地把我那一票画上去（lib/reactions 的乐观更新）。 */
  me: string
}

export function useMessageReactions(deps: MessageReactionsDeps) {
  /** 哪一条消息的选择条开着（一次一条）。 */
  const reactionPickerFor = ref<string | null>(null)

  function applyReactions(blockId: string, reactions: ReactionAgg[]) {
    deps.pendingHistory()?.set(blockId, reactions)
    const m = deps.find(blockId)
    if (m) m.reactions = reactions
  }

  function onReact(m: Block, emoji: string) {
    reactionPickerFor.value = null
    void reactOptimistically(m.id, emoji, deps.me, {
      before: () => deps.find(m.id)?.reactions,
      apply: applyReactions,
      send: apiToggleReaction,
      fail: (e) => (deps.errorMsg.value = e instanceof Error ? e.message : t('work.room.chat.reactionFailed')),
    })
  }

  /** 表情选择条：点同一条收起，点另一条移过去。 */
  function togglePicker(blockId: string) {
    reactionPickerFor.value = reactionPickerFor.value === blockId ? null : blockId
  }

  return { reactionPickerFor, applyReactions, onReact, togglePicker }
}
