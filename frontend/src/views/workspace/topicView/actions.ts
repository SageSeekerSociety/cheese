// 对话栏和面板回上来说的那几件事，在这一页各归哪一处。
//
// 一张动作卡上的按钮（§3.1.1 控件）：点开相关的那一格——资料库里的一份文档开在面板的
// 自由区；「它此刻在干什么」去现场；本轮摘要的「查看改动」是隔壁一格，不是新一页；
// 「查看改动」还要在正文里一处处标出这个人让 AI 队友改的那几处。只有任务有自己的
// 文档；频道里说的是资料库里的那份，前面那条已经接住了。离开专注模式会重画编辑器、把
// 它的 DOM 重建一遍，所以要等那次重画落定再标亮闪一下，不然闪完立刻被抹掉。
//
// 一条被点开的 <@handle>：去他的成员页。
//
// 预览里指出的一处位置，作为一条普通消息进这个房间的对话：没有新接口，也没有长期锚点，
// 它只在下一轮被读一次。
import type { Ref } from 'vue'
import type TaskOverview from '@/components/task/TaskOverview.vue'
import type { AgentControlState, Block } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { PreviewLocate, SubmitPreviewQuestion } from '@/lib/previewQuestion'
import type { ChatColumnHandle, TopicPlace, WorkPanelHandle } from './shared'

import { computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'

import { personalAsker } from '@/lib/personalAsker'
import { isProposalResource, proposalPage } from '@/lib/proposalNavigation'
import { userRefRoute } from '@/lib/userRef'
import { agentAvailable, askAgent, openAgent } from '@/stores/agent'

/** 任务概览那一列往回够的那几处：标亮某一轮改的几处。组件自己暴露什么，这里就是什么。 */
export type TaskOverviewHandle = InstanceType<typeof TaskOverview>

export function useTopicActions(opts: {
  place: () => TopicPlace
  /** 面板浮层那一处：正文里标亮之前先把它打开。 */
  panel: { show: () => void }
  focusMode: Ref<boolean>
  onPanelTab: (key: string) => void
  onReview: () => void
  /** 采纳卡重读。 */
  reloadAccept: () => void
  /** 读这段对话里还在等人的技能提议：右侧那一格要画它。 */
  loadSkills: () => Promise<void>
  /** 支线：打开一条、开任务页、把一条消息转成任务。 */
  opening: {
    onOpenThread: (block: Block) => void
    onOpenCard: (taskId: string) => void
    upgradeMessage: (messageId: string) => void
  }
  openTopic: (topicId: string) => void
  panelRef: Ref<WorkPanelHandle | null>
  chatColumn: Ref<ChatColumnHandle | null>
  taskOverviewRef: Ref<TaskOverviewHandle | null>
  /** 现场那一格读的几样，由对话栏从 socket 上报上来。 */
  activity: Ref<MemberActivityLine[]>
  agentControl: Ref<AgentControlState | null>
  siteTurns: Ref<Record<string, number>>
  handleTurnDone: () => void
  handleWorking: (now: boolean) => void
  handleStateChanged: (resource: string) => void
}) {
  const router = useRouter()

  /** 预览里指出的一处位置，作为一条普通消息进这个房间的对话。 */
  function onLocate(payload: PreviewLocate) {
    opts.chatColumn.value?.say(payload.message, payload.attachments)
  }
  const submitQuestion: SubmitPreviewQuestion = (request) => opts.chatColumn.value?.submitQuestion(request) ?? false

  /** 预览里私下问个人芝士：开面板、把那句话问过去。
   *
   *  没有这一条（没登录）时面板不摆那一项——摆一条按下去没反应的比不摆更糟。
   *  「有没有」由这里说了算，面板只认「给了还是没给」。 */
  const askPersonal = computed(() =>
    personalAsker(agentAvailable.value, openAgent, (message) => void askAgent(message))
  )

  function handleMentionClick(handle: string) {
    void router.push(userRefRoute(handle, opts.place().projectId))
  }

  // 对话那一栏在两端挂在不同位置（左栏 / tab 栏第一格），但接的是同一组事件。
  const chatEvents = {
    'turn-done': opts.handleTurnDone,
    working: opts.handleWorking,
    activity: (lines: MemberActivityLine[]) => (opts.activity.value = lines),
    'agent-control': (state: AgentControlState) => (opts.agentControl.value = state),
    'site-block': (block: Block) => opts.panelRef.value?.siteBlock?.(block),
    'site-turns': (turns: Record<string, number>) => (opts.siteTurns.value = turns),
    'state-changed': opts.handleStateChanged,
    // 芝士摆出来一份东西：面板立刻看一眼当前预览，不等轮询。
    'preview-shown': () => opts.panelRef.value?.previewShown?.(),

    'mention-click': handleMentionClick,
    'open-file': (path: string) => opts.panelRef.value?.openFile?.(path),
    'open-resource': handleOpenResource,
    'upgrade-message': opts.opening.upgradeMessage,
    'open-thread': opts.opening.onOpenThread,
    'open-topic': opts.openTopic,
    'open-card': opts.opening.onOpenCard,
    review: opts.onReview,
  }

  // An action card's button → open the relevant view (§3.1.1 控件).
  async function handleOpenResource(
    resource: string,
    turnId?: string,
    review?: DocReviewRequest,
    document?: OpenedDocument
  ) {
    if (document && isProposalResource(resource)) {
      // AI 队友提议的技能：还在等人的，在面板的自由区开一格，读完就地决定；定下来的和
      // 例行事务去它在项目里的那一页。
      if (resource === 'skill' && opts.panelRef.value?.openSkill) {
        opts.focusMode.value = false
        void opts.loadSkills()
        await nextTick()
        await opts.panelRef.value.openSkill(document)
        return
      }
      const page = proposalPage(resource, opts.place().projectId, document.id)
      if (page) void router.push(page)
      return
    }
    if (resource === 'doc' && document) {
      // 资料库里的一份文档：在面板的自由区开一格，不是这个对话自己的文档。
      opts.focusMode.value = false
      await nextTick()
      await opts.panelRef.value?.openDocument?.(document, review)
      return
    }
    if (resource === 'site') {
      // 对话里在动的那个头像：它此刻在干什么，去现场看。
      opts.focusMode.value = false
      opts.onPanelTab('site')
    } else if (resource === 'changes') {
      // 本轮摘要的「查看改动」: the diff is a tab away, not a new page.
      opts.focusMode.value = false
      opts.onPanelTab('changes')
    } else if (resource === 'accept') {
      opts.reloadAccept()
    } else if (resource === 'doc') {
      // 只有任务有自己的文档；频道里说的是资料库里的那份，上面那条已经接住了。
      if (!opts.place().taskId) return
      opts.focusMode.value = false
      opts.panel.show()
      await opts.panelRef.value?.showOverview()
      // 「查看改动」：在正文里一处处标出这个人让 AI 队友改的那几处。
      if (review) opts.taskOverviewRef.value?.reviewEdits(review)
      else if (turnId) opts.taskOverviewRef.value?.highlightTurn(turnId)
      else opts.taskOverviewRef.value?.pulse()
    }
    // topics: the topic panel is already in view next to the chat.
  }

  return { chatEvents, handleOpenResource, handleMentionClick, onLocate, submitQuestion, askPersonal }
}
