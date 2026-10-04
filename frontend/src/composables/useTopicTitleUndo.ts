// 「撤销改名」这一下：RoomNotice 那一行的按钮按下去，把刚刚的一次自动改名撤回。
// 后端改完会发 `state: topics`，侧栏据此重读；这里再主动报一次，按下去就能看到名
// 字回来。失败就写在面板那行错误提示上——这是唯一还需要面板状态的地方，所以它作为
// 依赖传进来，而不是这里再造一个。
import type { Ref } from 'vue'
import type { Topic } from '../cx_types'
import type { ChatPanelEmit } from './chatPanelContract'

import { undoTopicTitle } from '../api'

import { t } from '@/i18n'

export function useTopicTitleUndo(deps: {
  topic: () => Topic | null
  emit: ChatPanelEmit
  errorMsg: Ref<string | null>
}): { undoTitle: (blockId: string) => Promise<void> } {
  async function undoTitle(blockId: string) {
    const room = deps.topic()
    if (!room) return
    try {
      await undoTopicTitle(room.id, blockId)
      deps.emit('state-changed', 'topics')
    } catch (e) {
      deps.errorMsg.value = e instanceof Error ? e.message : t('work.room.chat.undoFailed')
    }
  }
  return { undoTitle }
}
