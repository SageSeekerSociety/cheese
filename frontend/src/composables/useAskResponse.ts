/**
 * 一道题上自己的回应：跳过它，撤回自己在它上面的那一条，或者点它上面平台给的按钮。
 *
 * 跳过是被问的人说「这道题我不回答」：房间里不出现任何消息，题上记下这次跳过，题
 * 不再等他，提问的那位也会被告知。
 *
 * 一道题只收一个回应，点错一下就没有别的路可走，所以回应过的那位可以把它收回去。
 * 收回去的是**这道题上的那一条**，不是他说过的那句话：消息还留在房间里，正文一个字
 * 不动。题回到「还没人答」，选项重新长出来。撤销一次跳过也是这一步。
 *
 * 两条路的形状和改自己的消息（`useChatComposer.saveEdit`）一样：先请服务端改，回来
 * 的那一块原地换掉——不先改本地再补，失败了也不用回滚。自己这头的改动不推送，服务
 * 端改完会往房间里推一帧 `block_updated`，所有人（包括自己）看到的是同一份。
 *
 * 服务端的规矩：只有能回答这道题的人跳得过（`block.asked.may_answer`），只有回应过的那个人撤得动，别人都会被拒
 * （403）；按钮本来也只长在这两处。
 * 平台给的按钮（`agent.ask.ask_to_decide`：接受交付、关闭任务）发的就是卡上「采纳」、
 * 任务「关闭任务」发的那一个请求（`acceptCard` / `closeTask`），谁能点由那两个接口判断。
 * 题不在这里改：交付一有结果，服务端把题收起来并推一帧 `block_updated`
 * （`block.questions.settle_decision`），不管结果是在哪儿做出来的。
 *
 * 放在这个单独的文件里，是因为 `useChatPanel.ts` 已经贴着行数上限，一行都长不了。
 */
import type { Ref } from 'vue'
import type { Block } from '../cx_types'
import type { AskAction } from '../types/ask'
import type { AcceptCardState } from './useAcceptCard'

import { ref } from 'vue'

import { acceptCard, closeTask, skipQuestion as skipOnServer, withdrawAnswer } from '../api'

import { t } from '@/i18n'

export interface AskResponseDeps {
  /** 房间里那一段块：改完的那一块原地换掉（`useTimeline.replace`）。 */
  timeline: { replace: (block: Block) => void }
  /** 失败时那面横幅。 */
  errorMsg: Ref<string | null>
  /** 看着这间房的人：采纳的请求带上他（后端照旧按登录的人认）。 */
  viewer: Readonly<Ref<string>>
  /** 任务页取的那一份采纳卡（`provideAcceptCard`）；题上的「采纳」问的是它那张卡时，
   *  点下去就是交付栏那一下（`onAcceptCard`）。不在任务页上是 null。 */
  acceptCard?: AcceptCardState | null
}

export function useAskResponse(deps: AskResponseDeps) {
  /** 已经有一次请求在路上：挡住连点两下发出两次。 */
  const busy = ref(false)

  async function respond(write: () => Promise<Block | null>, fallback: string) {
    if (busy.value) return
    busy.value = true
    deps.errorMsg.value = null
    try {
      const block = await write()
      if (block) deps.timeline.replace(block)
    } catch (e) {
      // 服务端拒掉时说的那句话（`noticeText` 已经把键翻成人话）优先，只是网络断了
      // 之类的才用本地的兜底那句。
      deps.errorMsg.value = e instanceof Error ? e.message : fallback
    } finally {
      busy.value = false
    }
  }

  const withdrawQuestionAnswer = (question: Block) =>
    respond(() => withdrawAnswer(question.id), t('ask.replies.withdrawFailed'))
  const skipQuestion = (question: Block) => respond(() => skipOnServer(question.id), t('ask.replies.skipFailed'))

  const decide = (_question: Block, action: AskAction) =>
    respond(async () => {
      const bar = deps.acceptCard
      if (action.kind === 'accept' && bar?.pendingCard.value?.id === action.card) await bar.onAcceptCard()
      else if (action.kind === 'accept')
        await acceptCard(action.card, deps.viewer.value, action.head_sha, action.deliverable_revision)
      else await closeTask(action.task)
      return null
    }, t('ask.replies.decideFailed'))

  return { withdrawQuestionAnswer, skipQuestion, decide, busy }
}
