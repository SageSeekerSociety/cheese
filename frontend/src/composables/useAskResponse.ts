/**
 * 一道题上自己的回应：跳过它，或者把那次跳过收回来。
 *
 * 跳过是被问的人说「这道题我不回答」：房间里不出现任何消息，题上记下这次跳过，题
 * 不再等他，提问的那位也会被告知。它只把题从他手上拿走：题问了几个人，别人照旧等
 * 着答。撤销跳过就是把题放回自己清单里，选项重新长出来。
 *
 * **回答撤不回来。** 答了就是答了——那是他给提问那位的答复，题上记着、房间里那条
 * 消息也还在，所以这里只有跳过这一条路，拿一个答案去撤会被服务端拒掉
 * （`askAnswerIsFinal`）。答错了就在房间里说一句，让芝士自己决定怎么办。
 *
 * 两条路的形状和改自己的消息（`useChatComposer.saveEdit`）一样：先请服务端改，回来
 * 的那一块原地换掉——不先改本地再补，失败了也不用回滚。自己这头的改动不推送，服务
 * 端改完会往房间里推一帧 `block_updated`，所有人（包括自己）看到的是同一份。
 *
 * 服务端的规矩：只有能回答这道题的人跳得过（`block.asked.may_answer`），也只有他
 * 自己撤得回来，别人都会被拒（403）；按钮本来只长在那一处。
 * 放在这个单独的文件里，是因为 `useChatPanel.ts` 已经贴着行数上限，一行都长不了。
 */
import type { Ref } from 'vue'
import type { Block } from '../cx_types'

import { ref } from 'vue'

import { skipQuestion as skipOnServer, unskipQuestion } from '../api'

import { t } from '@/i18n'

export interface AskResponseDeps {
  /** 房间里那一段块：改完的那一块原地换掉（`useTimeline.replace`）。 */
  timeline: { replace: (block: Block) => void }
  /** 失败时那面横幅。 */
  errorMsg: Ref<string | null>
}

export function useAskResponse(deps: AskResponseDeps) {
  /** 已经有一次请求在路上：挡住连点两下发出两次。 */
  const busy = ref(false)

  async function respond(write: () => Promise<Block>, fallback: string) {
    if (busy.value) return
    busy.value = true
    deps.errorMsg.value = null
    try {
      deps.timeline.replace(await write())
    } catch (e) {
      // 服务端拒掉时说的那句话（`noticeText` 已经把键翻成人话）优先，只是网络断了
      // 之类的才用本地的兜底那句。
      deps.errorMsg.value = e instanceof Error ? e.message : fallback
    } finally {
      busy.value = false
    }
  }

  const undoSkip = (question: Block) => respond(() => unskipQuestion(question.id), t('ask.replies.skipFailed'))
  const skipQuestion = (question: Block) => respond(() => skipOnServer(question.id), t('ask.replies.skipFailed'))

  return { undoSkip, skipQuestion, busy }
}
