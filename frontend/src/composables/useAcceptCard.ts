// 成果待采纳框 (eval C5/A3) 的那一半：卡从哪来、现在是哪一张在台面上、按下去
// 会打到哪个端点。
//
// 这个框拥有自己的数据（卡的清单、PR 检查的轮询），而不是把数据当 props 收进来：
// 这里每一件事都只关乎**这一个话题的这几张卡**，外面没有人要读它们。TopicView
// 只会说一句「重新拉一次」（`reload`），它说这句的时候是芝士递了卡、或者一条
// `cheese` 命令在中途改了卡。
//
// 画法在 `components/accept/*.vue`，接线在 `components/TopicAcceptCard.vue`。
// 拆自那个 1215 行的组件（#2143），注释跟着它解释的那段代码走了一遍。
import type { AcceptCard, MergeReason, PrChecks } from '@/cx_types'
import type { MergeBadge } from '@/lib/mergeState'
import type { CardPhase } from '@/lib/topicState'

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import { useUserRef } from '@/composables/useUserRef'

import {
  acceptCard,
  approveCard,
  cardDeliverableUrl,
  downloadFile,
  getAcceptCards,
  getPrChecks,
  mergeCardAnyway,
  reassignCard,
  rejectCard,
  revokeCard,
  setAutoMerge,
  voidCard,
} from '@/api'
import { t } from '@/i18n'
import { mergeBadgeOf, visibleReasons } from '@/lib/mergeState'
import { noteTone } from '@/lib/noteTone'
import { reconcile } from '@/lib/reconcile'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

/** 这个框从宿主那里知道的全部：是哪个话题的卡、看的是哪一条活的卡、贴不贴底。 */
export interface AcceptCardHost {
  topicId: string
  topicStatus: string
  taskId?: string | null
  docked?: boolean
}

export function useAcceptCard(props: AcceptCardHost) {
  const store = useWorkspaceStore()
  const AUTHOR = myHandle()

  const acceptCards = ref<AcceptCard[]>([])
  // This topic's cards are in. Distinguishes 「没有卡」 from 「还没问过」 for anyone
  // reading the phase from outside.
  const loaded = ref(false)
  // 卡的出现和收走演不演。打开房间 / 切话题那一次读到的不演（那张卡本来就在），读完
  // 之后再变的才演。
  const animate = ref(false)
  const acceptBusy = ref(false)
  const rejectNote = ref('')
  const showRejectInput = ref(false)
  // 作废：卡停在一个没人能推进的地方（GitHub 拒绝合并、冲突卡等）时的出口。
  // 它不是退回——不叫芝士改，只结束这次审阅，所以同样要先展开、再确认。
  const voidNote = ref('')
  const showVoidInput = ref(false)
  // 人工放行 (#718): 明知合并态不是 clean 仍合并。默认拒绝、显式放行，所以
  // 它藏在一个要先展开、再填理由的小表单后面——不是一个可以顺手点到的按钮。
  const showForceMergeInput = ref(false)
  const forceMergeReason = ref('')

  // Newest pending card (the list comes newest-first). A card in `conflict`
  // (采纳时合并冲突，芝士被派去解决) keeps the merge box up — as a STATE, with a
  // retry button — instead of pretending the accept went through.
  const pendingCard = computed<AcceptCard | null>(
    () => acceptCards.value.find((c) => c.status === 'pending' || c.status === 'conflict') ?? null
  )
  // The accepted card on an archived topic — its presence lets us offer 撤回采纳.
  const acceptedCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'accepted') ?? null)

  // 已采纳等合并 (`pr_open`, #718 退役): 历史状态。采纳现在当场合并，什么都不再
  // 写这个状态，存量卡也已迁回 pending —— 这张脸和闸门那两张一样，只为库里的
  // 极端残留兜底，只读。
  const deliveringCard = computed<AcceptCard | null>(
    () => acceptCards.value.find((c) => c.status === 'pr_open') ?? null
  )
  // 后端把途中的阶段信息/故障写在卡的 note 上（PR 有新提交、GitHub 拒绝合并、
  // 凭据失效），那是这些事唯一露头的地方，照原样显示。轻重由 note_level 定。
  const cardNote = (card: AcceptCard | null) => {
    if (!card) return null
    const tone = noteTone(card)
    return tone ? { text: card.note, tone } : null
  }
  const deliveryNote = computed(() => cardNote(deliveringCard.value))
  // 待采纳卡上的同一条 note（比如「PR 有新提交，之前看到的版本已过时」）。冲突卡
  // 的 note 已经在冲突说明里念过了，不再重复。
  const pendingNote = computed(() =>
    pendingCard.value && pendingCard.value.status !== 'conflict' ? cardNote(pendingCard.value) : null
  )

  // 卡上的状态 = 合并态 (#718)：词和「谁的活」的圈都是后端算好的，这里只翻译
  // （lib/mergeState.ts）。冲突卡的标题已经说了「芝士处理中」，不再画第二行。
  const mergeBadge = computed<MergeBadge | null>(() => {
    const card = pendingCard.value
    if (!card || card.status === 'conflict') return null
    return mergeBadgeOf(card.merge_state, store.agentName)
  })
  const mergeReasons = computed<MergeReason[]>(() => {
    const card = pendingCard.value
    if (!card) return []
    return card.forge.reports_checks && card.pr_number === null
      ? card.merge_state.reasons
      : visibleReasons(card.merge_state)
  })
  // 读不到检查结论的托管方，采纳就是验收人自己的判断。
  const platformLane = computed(() => {
    const card = pendingCard.value
    return !!card && !card.forge.reports_checks
  })
  // 这个托管方在人点之前就说明了自己是谁（I23）；GitHub 那一档不说话，卡上有链接。
  const forgeDeclaration = computed(() => pendingCard.value?.forge.declaration || '')
  const needsPr = computed(() => !!pendingCard.value?.forge.hosts_proposals && pendingCard.value.pr_number === null)
  // 按钮亮不亮，跟后端的采纳闸门是同一条线（domain/review/merge_state.py +
  // services.py）：`clean` 与 `unstable` 后端会合，按钮就亮；`blocked` /
  // `behind` / `dirty` / `unknown` 后端会 422 拒，按钮就灰，title 说明为什么。
  // unstable 是「有检查没过，但没有一个在必跑名单上」——它可以合，而红了哪个检查
  // 照样念在按钮上方的依据行里（mergeReasons），亮着不等于不说。
  // 灰之前按住的那半条路（人工放行）也用这个判断，两个入口不许对「现在能不能合」
  // 有两种看法。
  const MERGEABLE_STATES = ['clean', 'unstable']
  const acceptBlockedTitle = computed<string | null>(() => {
    const card = pendingCard.value
    if (!card) return null
    // 托管方读不出来的那一档（forge.kind === 'unknown'）：能力位一位都不敢说是，所以
    // 它看起来像平台 lane，但它不是 —— 后端这会儿真去采纳会按同一个失败 422 拒掉。
    // 闸门和采纳是同一条线，那就灰在这里，理由用卡上已经写着的那一句。
    if (card.forge.kind === 'unknown') return card.forge.declaration
    if (platformLane.value || needsPr.value) return null
    if (MERGEABLE_STATES.includes(card.merge_state.state)) return null
    const why = mergeReasons.value.map((r) => r.detail).filter(Boolean)
    return [t('topic.accept.notMergingNow'), ...why].join(t('topic.accept.separator'))
  })

  // 绿了自动合 (#718)：项目允许、且卡正停在 blocked/behind（规则还没满足）时才有
  // 这个开关；已布防的开关一直可见，好让人解除。
  const autoMergeArmedBy = computed(() => pendingCard.value?.auto_merge.armed_by ?? null)
  const autoMergeVisible = computed(() => {
    const card = pendingCard.value
    if (!card || !card.auto_merge.allowed) return false
    const state = card.merge_state.state
    return state === 'blocked' || state === 'behind' || !!card.auto_merge.armed_by
  })

  // 机器闸门 (eval C2, 已退役): a card left in a gate state by the mechanism that
  // used to run the project's check before the card reached its reviewer. Only the
  // newest card can carry one (one live card per topic is enforced server-side).
  const gateCard = computed<AcceptCard | null>(() => {
    const c = acceptCards.value[0]
    return c && (c.status === 'gate_failed' || c.status === 'gate_blocked') ? c : null
  })
  const showGateOutput = ref(false)

  // 横条展开没有。默认收着：一行已经说清「有一个决定在等谁」，整张卡要的时候再看。
  const expanded = ref(false)
  const showDetail = computed(() => !props.docked || expanded.value)

  // The bar line is a plain string and cannot hold a UserRef, so the person is named
  // here with the same display name a UserRef would draw (#2764); a handle the roster
  // does not know stays a handle.
  const reviewerName = useUserRef(() => pendingCard.value?.reviewer_handle).label
  const deciderName = useUserRef(() => acceptedCard.value?.decided_by).label

  // 横条上那一行说什么。顺序和下面卡片的 v-if 链一致：同一时刻只有一张卡在台面上。
  const bar = computed<{ icon: string; color: string; title: string; sub: string }>(() => {
    const gate = gateCard.value
    if (gate?.status === 'gate_failed')
      return { icon: 'mdi-close-octagon-outline', color: 'error', title: t('work.room.accept.gateFailed'), sub: '' }
    if (gate?.status === 'gate_blocked')
      return { icon: 'mdi-help-circle-outline', color: 'warning', title: t('work.room.accept.gateBlocked'), sub: '' }
    const pending = pendingCard.value
    if (pending?.status === 'conflict')
      return {
        icon: 'mdi-source-merge',
        color: 'warning',
        title: t('work.room.accept.conflict', { agent: store.agentName }),
        sub: '',
      }
    if (pending)
      return {
        icon: 'mdi-source-merge',
        color: 'success',
        // 被审阅的东西按它实际是什么说。交一次合并时，产物是整个代码仓库，每张卡都是
        // 「《同一个名字》第 N 版」，一行里说不出这次改了什么 —— 那就用这次改动自己的
        // 标题；产物和第几版在展开的「这次交付」里。交文件、交地址时，产物的名字和第几版
        // 就是这次交的东西。
        title:
          pending.deliverable?.kind === 'merge' && pending.change_subject
            ? pending.change_subject
            : pending.artifact
              ? t('work.room.accept.artifact', { name: pending.artifact.name, version: pending.artifact.version })
              : t('work.room.accept.change'),
        // 「待某人审阅」只在球真的在人手上（后端算的 who 是 human）时说：检查还在跑、
        // 芝士在修、平台在更新分支时，这一行说的是合并态那个词，和卡里的状态行同一个结论。
        sub:
          mergeBadge.value && pending.merge_state.who !== 'human'
            ? mergeBadge.value.label
            : pending.reviewer_handle === AUTHOR
              ? t('work.room.accept.waitingOnYou')
              : t('work.room.accept.waitingOn', { name: reviewerName.value }),
      }
    if (deliveringCard.value)
      return { icon: 'mdi-history', color: 'warning', title: t('work.room.accept.delivering'), sub: '' }
    const accepted = acceptedCard.value
    return {
      icon: 'mdi-check-circle-outline',
      color: 'success',
      title: accepted ? t('work.room.accept.decidedBy', { name: deciderName.value }) : t('work.room.accept.accepted'),
      sub: '',
    }
  })

  // Nothing to show at all — the host still renders the slot wrapper, so this
  // component simply contributes no box.
  const hasBox = computed(
    () =>
      !!(
        pendingCard.value ||
        gateCard.value ||
        deliveringCard.value ||
        (props.topicStatus === 'archived' && acceptedCard.value)
      )
  )

  // 两件事分开做：换了话题（或任务）才清空，那时屏幕上的卡属于别处；其余每一次重读
  // ——轮询、点完一个按钮、宿主说「重新拉一次」——都把读回来的那份合到屏幕上那份上，
  // 卡一直在，没变的卡连对象都不换（lib/reconcile.ts）。以前是一个 silent 开关由调
  // 用方自己选，按钮那几处选了不静默，于是每点一下整张卡消失、再带着入场动画长回来。
  function resetForTopic() {
    animate.value = false
    acceptCards.value = []
    loaded.value = false
    showRejectInput.value = false
    rejectNote.value = ''
    showGateOutput.value = false
    expanded.value = false
    showVoidInput.value = false
    voidNote.value = ''
    showForceMergeInput.value = false
    forceMergeReason.value = ''
  }

  // 后发的请求先回来、先发的后回来时，晚到的那份是旧的，不许盖掉新的。
  let requested = 0
  let applied = 0

  // `alongside`：和卡一起变的另一份数据（话题状态）。等它也回来再换卡：采纳之后卡先
  // 变成 accepted、话题还没归档的那一拍，框会先收走再长回来。
  async function loadAcceptCard(alongside?: Promise<unknown>) {
    const tid = props.topicId
    const task = props.taskId
    if (!tid) return
    const seq = ++requested
    try {
      // A task's cards are read through the task's own conversation.
      const [payload] = await Promise.all([getAcceptCards(task ?? tid), alongside?.catch(() => undefined)])
      if (props.topicId !== tid || props.taskId !== task || seq < applied) return
      applied = seq
      const first = !loaded.value
      acceptCards.value = reconcile(
        acceptCards.value,
        payload.data.filter((card) => (props.taskId ? card.task_id === props.taskId : !card.task_id))
      )
      loaded.value = true
      // 这个话题第一次读到的卡本来就在，不演入场；之后再出现、再收走的才演。
      if (first) void nextTick(() => (animate.value = true))
    } catch {
      // Best-effort; the cards on screen stay as they were.
    }
  }

  // 采纳 PR 化 (#188 §5.1): live CI state of the card's PR. Polled slowly while such
  // a card is on screen — checks take minutes, not seconds. Both the pending card
  // (人还没点) and the delivering one (点完了，CI 在跑) ride the same PR, and
  // /pr-checks answers for any card that has a pr_number.
  const prCheckCard = computed<AcceptCard | null>(() => pendingCard.value ?? deliveringCard.value)
  const prChecks = ref<PrChecks | null>(null)
  let prPollTimer: number | null = null
  async function loadPrChecks() {
    const tid = props.topicId
    const task = props.taskId
    if (!prCheckCard.value?.pr_number) return
    try {
      const payload = await getPrChecks(task ?? tid)
      if (props.topicId === tid && props.taskId === task) prChecks.value = payload
    } catch {
      // Best-effort; the PR row just shows the link without CI state.
    }
  }
  watch(
    () =>
      prCheckCard.value?.pr_number ? `${props.topicId}:${props.taskId ?? ''}:${prCheckCard.value.pr_number}` : null,
    (active) => {
      prChecks.value = null
      if (active) void loadPrChecks()
      if (active && prPollTimer === null) {
        prPollTimer = window.setInterval(() => {
          void loadPrChecks()
          // 卡本身也跟着刷——待采纳和交付中都要，而且理由是同一个：**这张卡是快照，
          // 而它描述的东西还在变**，界面不重新读就会停在它到达的那一刻。
          //
          // 交付中那一支变的是合并时间、note、最终 accepted。
          //
          // 待采纳那一支变的是 `merge_state`，而它决定采纳按钮亮不亮：递卡的一瞬间
          // PR 刚从草稿翻成待看，GitHub 还没算完能不能合，后端如实给 `unknown` ——
          // 不可采纳，注释里写着「下一轮读到真值自然收敛」（domain/review/
          // merge_state.py）。可这里原本没有下一轮，于是那颗按钮就一直灰着，验收人
          // 只能靠刷新页面或切一次话题才点得动。实测 #888：01:23 还是 unstable，
          // 01:24 已经 clean，后端确实在收敛，看不见的是界面。
          void loadAcceptCard()
        }, 15000)
      } else if (!active && prPollTimer !== null) {
        window.clearInterval(prPollTimer)
        prPollTimer = null
      }
    },
    { immediate: true }
  )
  onUnmounted(() => {
    if (prPollTimer !== null) window.clearInterval(prPollTimer)
  })

  // 这一版交出去的那一份，在人点采纳之前拿到手 (#1085 结论五)。走下载而不是预览：
  // 给的是递卡那一刻落下的快照，要审的就是这些字节本身。
  const deliverableBusy = ref(false)
  const deliverableError = ref('')

  async function onDownloadDeliverable() {
    const card = pendingCard.value
    const filename = card?.deliverable?.filename
    if (!card || !filename || deliverableBusy.value) return
    deliverableBusy.value = true
    deliverableError.value = ''
    try {
      await downloadFile(cardDeliverableUrl(card.id), filename)
    } catch (e) {
      deliverableError.value = e instanceof Error ? e.message : t('files.downloadFailed')
    } finally {
      deliverableBusy.value = false
    }
  }

  // 主分支保护 (spec §4.4): my vote toward the pending card's accept.
  async function onApproveCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await approveCard(card.id, AUTHOR)
      await loadAcceptCard()
    } catch (e) {
      store.reportError(e, t('topic.accept.approveFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  // 能改派给谁：名册上还在岗的那些。队友当验收人没问题——AI 队友和人的权限一样大
  // ——要挡的只有停用的队友：停用就是为了挡住新的活，而改派就是派活。后端的
  // `reviewer_handle` 只是个 handle，不校验这个人还在不在，点下去就是把卡停在一个没
  // 人驱动的实例名下，界面上还照样写着「等 @xxx 验收」。
  const reviewerChoices = computed(() => store.members.filter((m) => m.active !== false))

  async function onReassignCard(handle: string) {
    const card = pendingCard.value
    if (!card || handle === card.reviewer_handle) return
    acceptBusy.value = true
    try {
      await reassignCard(card.id, handle)
      await loadAcceptCard()
    } catch (e) {
      store.reportError(e, t('topic.accept.reassignFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onAcceptCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      const updated = await acceptCard(card.id, AUTHOR, card.merge_state.head_sha)
      if (updated.status === 'conflict') {
        store.error = t('topic.accept.conflict', { agent: store.agentName })
      }
      // 卡已经定了：开着的退回框和里面那半句理由不再适用。
      showRejectInput.value = false
      rejectNote.value = ''
      await loadAcceptCard(store.refreshTopicRow(props.topicId))
    } catch (e) {
      store.reportError(e, needsPr.value ? t('topic.accept.createPrFailed') : t('topic.accept.acceptFailed'))
      // 被拒的原因可能正是「你看到的版本已过时」——那就把屏幕换成新的那一版，
      // 否则人只能对着同一张旧卡再点一次，再被拒一次。
      await loadAcceptCard()
    } finally {
      acceptBusy.value = false
    }
  }

  async function onRevokeCard() {
    const card = acceptedCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await revokeCard(card.id, AUTHOR)
      // 卡已经定了：开着的退回框和里面那半句理由不再适用。
      showRejectInput.value = false
      rejectNote.value = ''
      await loadAcceptCard(store.refreshTopicRow(props.topicId))
    } catch (e) {
      store.reportError(e, t('topic.accept.undoFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onForceMerge() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await mergeCardAnyway(card.id, forceMergeReason.value, card.merge_state.head_sha)
      showForceMergeInput.value = false
      forceMergeReason.value = ''
      await loadAcceptCard(store.refreshTopicRow(props.topicId))
    } catch (e) {
      store.reportError(e, t('topic.accept.acceptFailed'))
      await loadAcceptCard() // 见 onAcceptCard：过时的那一版要换掉
    } finally {
      acceptBusy.value = false
    }
  }

  // 绿了自动合 (#718)：布防/解除都打同一个端点，布防人由后端从会话认定。
  async function onToggleAutoMerge(enabled: unknown) {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await setAutoMerge(card.id, !!enabled, card.merge_state.head_sha)
      await loadAcceptCard()
    } catch (e) {
      store.reportError(e, t('topic.accept.autoMergeFailed'))
      await loadAcceptCard() // 见 onAcceptCard：过时的那一版要换掉
    } finally {
      acceptBusy.value = false
    }
  }

  async function onRejectCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await rejectCard(card.id, AUTHOR, rejectNote.value)
      showRejectInput.value = false
      rejectNote.value = ''
      await loadAcceptCard(store.refreshTopicRow(props.topicId))
    } catch (e) {
      store.reportError(e, t('topic.accept.returnFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onVoidCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await voidCard(card.id, voidNote.value)
      showVoidInput.value = false
      voidNote.value = ''
      await loadAcceptCard(store.refreshTopicRow(props.topicId))
    } catch (e) {
      store.reportError(e, t('topic.accept.voidFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  watch(
    () => [props.topicId, props.taskId],
    () => {
      resetForTopic()
      void loadAcceptCard()
    },
    { immediate: true }
  )

  // 决策在聊天，审查在面板: where the topic stands is not this box's private
  // business. The header states it and the panel opens on the tab it calls for,
  // so the one word travels up rather than the card list travelling out.
  const phase = computed<CardPhase>(() => {
    if (deliveringCard.value) return 'delivering'
    if (gateCard.value) return 'gate'
    if (pendingCard.value) return 'pending'
    return null
  })

  return {
    // 宿主用得到的
    acceptBusy,
    expanded,
    showDetail,
    hasBox,
    bar,
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
    showRejectInput,
    rejectNote,
    showVoidInput,
    voidNote,
    showForceMergeInput,
    forceMergeReason,
    deliverableBusy,
    deliverableError,
    // 动作
    reload: () => loadAcceptCard(),
    onDownloadDeliverable,
    onApproveCard,
    onReassignCard,
    onAcceptCard,
    onRevokeCard,
    onForceMerge,
    onToggleAutoMerge,
    onRejectCard,
    onVoidCard,
    // 画的时候顺手要用的
    author: AUTHOR,
    agentName: computed(() => store.agentName),
    agentHandle: computed(() => store.agentHandle),
  }
}
