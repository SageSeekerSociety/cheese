/**
 * 撤回自己在一道题上的回答。
 *
 * 一道题只收一个回答，点错一下就没有别的路可走，所以答过的那位可以把它收回去。
 * 收回去的是**这道题上的那条答复**，不是他说过的那句话：消息还留在房间里，正文一
 * 个字不动。题回到「还没人答」，选项重新长出来，谁都能再选一次。
 *
 * 这条路的形状和改自己的消息（`useChatComposer.saveEdit`）一样：先请服务端改，回来
 * 的那一块原地换掉——不先改本地再补，失败了也不用回滚。自己这头的改动不推送，服务
 * 端改完会往房间里推一帧 `block_updated`，所有人（包括自己）看到的是同一份。
 *
 * 服务端的规矩只有一条：只有答过话的那个人撤得动，别人、以及没答过的人都会被拒
 * （403，话是「你没有回答过这道题」）；按钮本来也只长在自己那条答复旁边。
 * 放在这个单独的文件里，是因为 `useChatPanel.ts` 已经贴着行数上限，一行都长不了。
 */
import type { Ref } from 'vue'
import type { Block } from '../cx_types'

import { ref } from 'vue'

import { withdrawAnswer } from '../api'

import { t } from '@/i18n'

export interface AskWithdrawDeps {
  /** 房间里那一段块：改完的那一块原地换掉（`useTimeline.replace`）。 */
  timeline: { replace: (block: Block) => void }
  /** 失败时那面横幅。 */
  errorMsg: Ref<string | null>
}

export function useAskWithdraw(deps: AskWithdrawDeps) {
  /** 已经有一次撤回在路上：挡住连点两下发出两次请求。 */
  const withdrawing = ref(false)

  async function withdrawQuestionAnswer(question: Block) {
    if (withdrawing.value) return
    withdrawing.value = true
    deps.errorMsg.value = null
    try {
      const updated = await withdrawAnswer(question.id)
      deps.timeline.replace(updated)
    } catch (e) {
      // 服务端拒掉时说的那句话（`noticeText` 已经把键翻成人话）优先，只是网络断了
      // 之类的才用本地的兜底那句。
      deps.errorMsg.value = e instanceof Error ? e.message : t('ask.replies.withdrawFailed')
    } finally {
      withdrawing.value = false
    }
  }

  return { withdrawQuestionAnswer, withdrawing }
}
