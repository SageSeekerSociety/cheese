// 成果待采纳框 (eval C5/A3)：把几块拼起来的那一处，也是这个框对外的样子。
//
// 这里自己只做三件事：读话题和任务是哪一个、换话题时把屏幕上的东西收干净、把四块
// 拼成一个宿主认得的对象。TopicView 只会说一句「重新拉一次」（`reload`），它说这句
// 的时候是芝士递了卡、或者一条 `cheese` 命令在中途改了卡。
//
// 卡从哪来 → `acceptCard/cards.ts`；卡面上说什么 → `acceptCard/derive.ts`；横条上
// 那一行和相位 → `acceptCard/bar.ts`；按下去打到哪个端点 → `acceptCard/actions.ts`。
// 画法在 `components/accept/*.vue`，接线在 `components/TopicAcceptCard.vue`。
// 拆自那个 1215 行的组件（#2143），注释跟着它解释的那段代码走了一遍。
import type { InjectionKey } from 'vue'

import { computed, inject, provide, ref, watch } from 'vue'

import { useAcceptCardActions } from '@/composables/acceptCard/actions'
import { useAcceptCardBar } from '@/composables/acceptCard/bar'
import { useAcceptCards } from '@/composables/acceptCard/cards'
import { useAcceptCardDerive } from '@/composables/acceptCard/derive'
import { signInWithGithub } from '@/composables/useGithubSignIn'
import { noteAccepted, primeInvitePrompt } from '@/composables/useInvitePrompt'
import { useReviewComments } from '@/composables/useReviewComments'

import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

/** 这个框从宿主那里知道的全部：是哪个话题的卡、看的是哪一条活的卡。 */
export interface AcceptCardHost {
  topicId: string
  topicStatus: string
  taskId?: string | null
  /** 谁在看这张卡。默认是登录的那个账号；演示页没有登录的人，读者扮的是卡上点名的审阅人。 */
  viewer?: string
}

export function useAcceptCard(props: AcceptCardHost) {
  const store = useWorkspaceStore()
  // 读成 computed：演示页里卡随剧本换，扮的审阅人跟着那张卡走。
  const me = computed(() => props.viewer ?? myHandle())
  const agentName = computed(() => store.agentName)
  const agentHandle = computed(() => store.agentHandle)

  // 话题和任务都读成函数交给下面几块：演示页和「改动」页里它们会换，读的时候才取值。
  const topicId = () => props.topicId
  const taskId = () => props.taskId

  const cards = useAcceptCards({ topicId, taskId })
  const { loaded, animate, pendingCard, acceptedCard, deliveringCard, gateCard, prChecks, load, reset } = cards

  // 等他采纳的卡一出现，先看一眼他的邀请码解锁了没有：采纳之后才知道那是不是第一次
  // （composables/useInvitePrompt.ts）。演示页里读者扮的是别人，不问。
  watch(
    pendingCard,
    (card) => {
      if (card && !props.viewer && card.reviewer_handle === me.value) void primeInvitePrompt()
    },
    { immediate: true }
  )
  // 「检查过了自动合并」或合并队列：点下去时卡还没合，合上是后来轮询看到的。那时卡上
  // 记着是他采纳的，也算他的一次采纳（`noteAccepted` 只认第一次，重复调用无妨）。
  watch(
    () => acceptedCard.value?.decided_by,
    (by) => {
      if (by && !props.viewer && by === me.value) void noteAccepted()
    }
  )

  // 待审阅时写的批注，退回时随卡送出（composables/useReviewComments.ts）。
  const comments = useReviewComments({ taskId: () => props.taskId, pendingCard: () => pendingCard.value })

  // 卡面上说什么，全靠读卡得出来，不碰网络（acceptCard/derive.ts）。
  const {
    pendingNote,
    deliveryNote,
    mergeBadge,
    mergeReasons,
    platformLane,
    forgeDeclaration,
    needsPr,
    acceptBlockedTitle,
    autoMergeArmedBy,
    autoMergeVisible,
    reviewerName,
    deciderName,
    decisionOpen,
    acceptLabel,
    forceMergeVisible,
    reviewerChoices,
  } = useAcceptCardDerive({ store, me, pendingCard, acceptedCard, deliveringCard })

  // 横条那一行、那个圈、有没有东西可画、以及对外说的相位（acceptCard/bar.ts）。
  const { bar, barColumn, hasBox, phase } = useAcceptCardBar({
    me,
    agentName,
    topicStatus: () => props.topicStatus,
    pendingCard,
    acceptedCard,
    deliveringCard,
    gateCard,
    mergeBadge,
    reviewerName,
    deciderName,
  })

  // 历史卡（闸门那两张、交付中那张）的横条展开没有。待审阅的卡没有展开：它的详情在
  // 「改动」页顶部，横条上只有状态和两颗决定按钮。
  const expanded = ref(false)
  const showGateOutput = ref(false)

  // 换了话题（或任务）才清空：那时屏幕上的卡、开着的小表单都属于别处。其余每一次重读
  // ——轮询、点完一个按钮、宿主说「重新拉一次」——卡一直留在屏幕上，没变的卡连对象都
  // 不换（acceptCard/cards.ts 的 `load`）。
  function resetForTopic() {
    reset()
    showGateOutput.value = false
    expanded.value = false
    actions.resetForms()
  }

  // 按下去打到哪个端点（acceptCard/actions.ts）：采纳、退回、作废、人工放行、改派、
  // 撤回采纳、布防自动合并、下载这一版交出去的那一份。它们读的那几张卡由上面拼好。
  // 这里的 `reload` 不是返回给宿主的那一只：写操作会把「要一起等的话题行刷新」当实参
  // 递进来（`reload(store.refreshTopicRow(topicId))`），形参必须原样转给 `load`，否则
  // 卡会比话题行先换，框先收走再长回来。
  const actions = useAcceptCardActions({
    store,
    topicId,
    me,
    pendingCard,
    acceptedCard,
    needsPr,
    comments,
    reload: (alongside?: Promise<unknown>) => load(alongside),
  })

  watch(
    () => [props.topicId, props.taskId],
    () => {
      resetForTopic()
      void load()
    },
    { immediate: true }
  )

  return {
    // 宿主用得到的
    acceptBusy: actions.acceptBusy,
    expanded,
    hasBox,
    bar,
    barColumn,
    decisionOpen,
    acceptLabel,
    forceMergeVisible,
    phase,
    loaded,
    // 台面上的那几张卡
    pendingCard,
    acceptedCard,
    deliveringCard,
    gateCard,
    // 待采纳卡面上的判断
    mergeBadge,
    mergeReasons,
    forgeDeclaration,
    needsPr,
    acceptBlockedTitle,
    platformLane,
    autoMergeVisible,
    autoMergeArmedBy,
    pendingNote,
    deliveryNote,
    reviewerChoices,
    prChecks,
    // 卡出现/收走演不演，由宿主交给 <Transition>。
    animate,
    // 展开的小表单
    showGateOutput,
    showRejectInput: actions.showRejectInput,
    rejectNote: actions.rejectNote,
    showVoidInput: actions.showVoidInput,
    voidNote: actions.voidNote,
    showForceMergeInput: actions.showForceMergeInput,
    forceMergeReason: actions.forceMergeReason,
    deliverableBusy: actions.deliverableBusy,
    // 这件任务的批注
    comments,
    // 动作
    reload: () => load(),
    onDownloadDeliverable: actions.onDownloadDeliverable,
    onApproveCard: actions.onApproveCard,
    onReassignCard: actions.onReassignCard,
    onAcceptCard: actions.onAcceptCard,
    onRevokeCard: actions.onRevokeCard,
    onForceMerge: actions.onForceMerge,
    onSignInGithub: signInWithGithub,
    onToggleAutoMerge: actions.onToggleAutoMerge,
    onRejectCard: actions.onRejectCard,
    onVoidCard: actions.onVoidCard,
    // 画的时候顺手要用的
    author: me,
    agentName,
    agentHandle,
  }
}

export type AcceptCardState = ReturnType<typeof useAcceptCard>

const ACCEPT_CARD: InjectionKey<AcceptCardState> = Symbol('acceptCard')

// 一个页面上同一张卡有好几处要读：对话栏的横条、「改动」页顶部，以及专注模式里面板
// 底部那一条。各自取一份的话，在一处点了采纳，另一处还停在点之前的样子，两份轮询
// 也各打一遍。所以页面取一份、往下发，几处读的是同一份。
export function provideAcceptCard(host: AcceptCardHost): AcceptCardState {
  const state = useAcceptCard(host)
  provide(ACCEPT_CARD, state)
  return state
}

/** 上面那一份；不在这样的页面里（预览站、单独渲染时）是 null，调用方自己取。 */
export function injectAcceptCard(): AcceptCardState | null {
  return inject(ACCEPT_CARD, null)
}
