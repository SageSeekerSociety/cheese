<script setup lang="ts">
// 成果待采纳框 (eval C5/A3): the box at the end of the conversation timeline,
// GitHub's merge box in shape. It has five mutually exclusive faces — 闸门未通过
// / 闸门未能执行 / 待采纳 / 已采纳等合并 / 已采纳 — and each of them says a
// different thing about who is waiting on whom.
//
// The first two are read-only history. 采纳即合并 (#296, stage 1) retired the
// machine gate, so nothing files a card into a gate state any more; rows written
// before that still carry it and still have to render (see
// backend/app/domain/review/gate.py). 已采纳等合并 (`pr_open`) is history too:
// #718 made accept merge on the spot, nothing writes the status any more and
// the stock was migrated back to pending — the face only covers stray relics.
//
// 待采纳 wears the merge state (#718): the card's word IS the backend's
// `merge_state` verdict, the dot next to it says whose move it is (the board's
// 该谁动 dot language), and the accept button lights only when clicking it
// would actually merge — except on the platform lane, where accepting is
// purely a human judgment.
//
// It owns its own data (the card list, the PR-checks poll) rather than taking
// them as props: everything here is about this one topic's cards and nothing
// outside needs to read them. TopicView only ever says 「重新拉一次」 (`reload`),
// which it does when 芝士 files a card or a `cheese` command changes one
// mid-turn.
import type { AcceptCard, PrChecks } from '@/cx_types'
import type { CardPhase } from '@/lib/topicState'

import { computed, onUnmounted, ref, watch } from 'vue'

import {
  acceptCard,
  approveCard,
  getAcceptCards,
  getPrChecks,
  mergeCardAnyway,
  reassignCard,
  rejectCard,
  revokeCard,
  setAutoMerge,
} from '@/api'
import { columnDotStyle } from '@/lib/board'
import { mergeBadgeOf, visibleReasons } from '@/lib/mergeState'
import { noteTone } from '@/lib/noteTone'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ topicId: string; topicStatus: string }>()
const emit = defineEmits<{
  (e: 'phase', phase: CardPhase): void
  /** 去验收: show me what I am being asked to accept. */
  (e: 'review'): void
}>()
const store = useWorkspaceStore()
const AUTHOR = myHandle()

const acceptCards = ref<AcceptCard[]>([])
// This topic's cards are in. Distinguishes 「没有卡」 from 「还没问过」 for anyone
// reading the phase from outside.
const loaded = ref(false)
const acceptBusy = ref(false)
const rejectNote = ref('')
const showRejectInput = ref(false)
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
const deliveringCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'pr_open') ?? null)
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
const mergeBadge = computed(() => {
  const card = pendingCard.value
  if (!card || card.status === 'conflict') return null
  return mergeBadgeOf(card.merge_state)
})
const mergeReasons = computed(() => {
  const card = pendingCard.value
  if (!card) return []
  return card.has_external_checks && card.pr_number === null
    ? card.merge_state.reasons
    : visibleReasons(card.merge_state)
})
// Projects without external checks leave acceptance to the reviewer.
const platformLane = computed(() => {
  const card = pendingCard.value
  return !!card && !card.has_external_checks
})
const needsPr = computed(() => !!pendingCard.value?.has_external_checks && pendingCard.value.pr_number === null)
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
  if (!card || platformLane.value || needsPr.value) return null
  if (MERGEABLE_STATES.includes(card.merge_state.state)) return null
  const why = mergeReasons.value.map((r) => r.detail).filter(Boolean)
  return ['现在采纳不会合并', ...why].join('：')
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

async function loadAcceptCard(silent = false) {
  // silent = a background refresh (the PR-checks poll / after a vote): keep the
  // current cards on screen instead of blanking the box for a beat.
  if (!silent) {
    acceptCards.value = []
    loaded.value = false
    showRejectInput.value = false
    rejectNote.value = ''
    showGateOutput.value = false
  }
  const tid = props.topicId
  if (!tid) return
  try {
    const payload = await getAcceptCards(tid)
    if (props.topicId === tid) {
      acceptCards.value = payload.data
      loaded.value = true
    }
  } catch {
    // Best-effort; the banner just stays hidden.
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
  if (!prCheckCard.value?.pr_number) return
  try {
    const payload = await getPrChecks(tid)
    if (props.topicId === tid) prChecks.value = payload
  } catch {
    // Best-effort; the PR row just shows the link without CI state.
  }
}
watch(
  () => (prCheckCard.value?.pr_number ? props.topicId : null),
  (active) => {
    prChecks.value = null
    if (active && prPollTimer === null) {
      void loadPrChecks()
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
        void loadAcceptCard(true)
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

// 主分支保护 (spec §4.4): my vote toward the pending card's accept.
async function onApproveCard() {
  const card = pendingCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await approveCard(card.id, AUTHOR)
    await loadAcceptCard(true)
  } catch (e) {
    store.reportError(e, '批准失败')
  } finally {
    acceptBusy.value = false
  }
}

async function onReassignCard(handle: string) {
  const card = pendingCard.value
  if (!card || handle === card.reviewer_handle) return
  acceptBusy.value = true
  try {
    await reassignCard(card.id, handle)
    await loadAcceptCard()
  } catch (e) {
    store.reportError(e, '改验收人失败')
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
      store.error = '采纳时出现合并冲突，本次未合并。芝士正在解决，完成后可重试采纳。'
    }
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, needsPr.value ? '创建 PR 未完成' : '采纳失败')
    // 被拒的原因可能正是「你看到的版本已过时」——那就把屏幕换成新的那一版，
    // 否则人只能对着同一张旧卡再点一次，再被拒一次。
    await loadAcceptCard(true)
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
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '撤回采纳失败')
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
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '人工放行失败')
    await loadAcceptCard(true) // 见 onAcceptCard：过时的那一版要换掉
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
    await loadAcceptCard(true)
  } catch (e) {
    store.reportError(e, '设置自动合并失败')
    await loadAcceptCard(true) // 见 onAcceptCard：过时的那一版要换掉
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
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '退回失败')
  } finally {
    acceptBusy.value = false
  }
}

watch(
  () => props.topicId,
  () => void loadAcceptCard(),
  { immediate: true }
)

// 决策在聊天，审查在面板: the card stays here — accepting is a social decision
// and needs the conversation around it — but where the topic stands is not this
// box's private business. The header states it and the panel opens on the tab it
// calls for, so the one word travels up rather than the card list travelling out.
//
// It is reported only once the cards are actually in: before that, 「没有卡」 and
// 「卡还没拉回来」 look identical from outside, and the panel would open on 文档
// for a topic that was waiting to be reviewed.
const phase = computed<CardPhase>(() => {
  if (deliveringCard.value) return 'delivering'
  if (gateCard.value) return 'gate'
  if (pendingCard.value) return 'pending'
  return null
})
watch([loaded, phase], () => {
  if (loaded.value) emit('phase', phase.value)
})

defineExpose({ reload: loadAcceptCard })
</script>

<template>
  <template v-if="hasBox">
    <!-- 闸门未过：卡片作废，芝士已被通知去修，修完会重新递卡。 -->
    <v-card v-if="gateCard && gateCard.status === 'gate_failed'" variant="outlined" class="merge-box mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="error" size="19">mdi-close-octagon-outline</v-icon>
          <span class="t-title">平台检查未通过</span>
        </div>
        <div class="text-caption text-medium-emphasis mb-2">
          这张验收卡没有送出。芝士已收到检查结果，会修复后重新提交。
        </div>
        <v-btn
          size="small"
          variant="text"
          :prepend-icon="showGateOutput ? 'mdi-chevron-up' : 'mdi-chevron-down'"
          @click="showGateOutput = !showGateOutput"
        >
          {{ showGateOutput ? '收起输出' : '查看输出' }}
        </v-btn>
        <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '暂无输出' }}</pre>
      </div>
    </v-card>

    <!-- 闸门没跑成：检查本身没能在门禁容器里跑起来，对代码没有结论。刻意跟
         「未通过」分开显示——它是需要人看一眼的状态，不是代码红了。 -->
    <v-card v-else-if="gateCard && gateCard.status === 'gate_blocked'" variant="outlined" class="merge-box mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="warning" size="19">mdi-help-circle-outline</v-icon>
          <span class="t-title">平台检查未能执行</span>
        </div>
        <div class="text-caption text-medium-emphasis mb-2">
          检查程序未能启动，因此它对这次改动<strong>没有结论</strong>——既不是通过，也不是未通过。
          这张验收卡没有送出。芝士已收到通知，会先恢复检查环境再重新提交；如果反复启动失败，需要人工介入。
        </div>
        <v-btn
          size="small"
          variant="text"
          :prepend-icon="showGateOutput ? 'mdi-chevron-up' : 'mdi-chevron-down'"
          @click="showGateOutput = !showGateOutput"
        >
          {{ showGateOutput ? '收起输出' : '查看输出' }}
        </v-btn>
        <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '暂无输出' }}</pre>
      </div>
    </v-card>

    <v-card v-else-if="pendingCard" variant="outlined" class="merge-box mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon :color="pendingCard.status === 'conflict' ? 'warning' : 'success'" size="19">
            mdi-source-merge
          </v-icon>
          <span class="t-title">
            {{ pendingCard.status === 'conflict' ? '合并冲突 · 芝士处理中' : '成果待采纳' }}
          </span>
        </div>
        <div v-if="pendingCard.status === 'conflict'" class="text-caption text-medium-emphasis mb-2">
          {{ pendingCard.note || '采纳时出现合并冲突，芝士正在解决。' }}
          它完成后可重试采纳。
        </div>
        <!-- 合并态 (#718): 状态词 + 「谁的活」的圈。词和 who 都是后端算好下发的，
             圈用看板「该谁动」的点语言（同一个问题在整套界面里只有一种颜色）。
             clean 画绿勾不画圈 —— 绿勾本身就是记号。 -->
        <div v-if="mergeBadge" class="d-flex align-center ga-2 text-body-2 mb-1">
          <v-icon v-if="pendingCard.merge_state.state === 'clean'" color="success" size="16">mdi-check-circle</v-icon>
          <span v-else class="board-dot" :style="columnDotStyle(mergeBadge.column)" aria-hidden="true" />
          <span>{{ mergeBadge.label }}</span>
        </div>
        <!-- 结论的依据：红了哪个检查要能看见。 -->
        <div
          v-for="(r, i) in mergeReasons"
          :key="i"
          class="d-flex align-center flex-wrap ga-1 text-caption text-medium-emphasis mb-1"
        >
          <span>{{ r.detail }}</span>
          <code v-for="chk in r.checks" :key="chk" class="text-caption">{{ chk }}</code>
        </div>
        <!-- 后端写在卡上的 note（比如「PR 有新提交，之前看到的版本已过时」）。 -->
        <div
          v-if="pendingNote"
          class="d-flex align-start ga-1 text-caption mb-2"
          :class="pendingNote.tone === 'error' ? 'text-error' : 'text-medium-emphasis'"
        >
          <v-icon v-if="pendingNote.tone === 'error'" icon="mdi-alert-circle-outline" size="14" class="mt-1" />
          <span>{{ pendingNote.text }}</span>
        </div>
        <div class="d-flex align-center flex-wrap ga-1 text-body-2 mb-1">
          <span>等</span>
          <strong>@{{ pendingCard.reviewer_handle }}</strong>
          <span>验收</span>
          <!-- 改验收人 (spec §4.4): 任何成员都可以改推荐/加人 -->
          <v-menu>
            <template #activator="{ props: menuProps }">
              <v-btn
                v-bind="menuProps"
                size="x-small"
                variant="text"
                density="comfortable"
                class="text-medium-emphasis"
                :disabled="acceptBusy"
              >
                改派
              </v-btn>
            </template>
            <v-list density="compact">
              <v-list-subheader>改派验收人</v-list-subheader>
              <v-list-item
                v-for="mbr in store.members"
                :key="mbr.user_handle"
                :active="mbr.user_handle === pendingCard.reviewer_handle"
                @click="onReassignCard(mbr.user_handle)"
              >
                <v-list-item-title class="text-body-2"> @{{ mbr.user_handle }} </v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ mbr.role }}
                </v-list-item-subtitle>
              </v-list-item>
              <v-list-item v-if="store.members.length === 0">
                <v-list-item-title class="text-caption text-medium-emphasis"> 暂无可选成员 </v-list-item-title>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>
        <div v-if="pendingCard.routing_reason" class="text-caption text-medium-emphasis mb-3">
          推荐理由：{{ pendingCard.routing_reason }}
        </div>
        <!--
          提交与 PR 规范: 采纳会把整个分支压成一个提交，标题就是这一行。
          采纳前是最后一次能反对它的机会，所以它必须在按钮上方可见，而不是
          等它进了 git 历史才有人发现写的是话题标题。
        -->
        <div v-if="pendingCard.change_subject" class="mb-3">
          <div class="text-caption text-medium-emphasis">合并后的提交标题</div>
          <code class="text-caption">{{ pendingCard.change_subject }}</code>
        </div>
        <!--
          机器闸门 (eval C2, 已退役) 的历史读数。`gate_passed_at` 只由
          `AcceptService.finish_gate` 写，而 采纳即合并 (#296, stage 1) 之后再没有
          任何东西调用它——所以今天递的卡这一格永远是空的，它出现就意味着这张卡是
          退役之前递的。留着，是因为那次检查当年真的跑过：抹掉等于把「这张卡当年
          过了平台检查」这个事实从界面上删掉。
          绝不画成绿勾：当年跑的是项目自己配的 check_command，不是完整 CI，让一个
          绿勾替它背书正是这一格要避免的事。今天的检查是 PR 上的 GitHub Actions，
          平台不会先替你跑一遍。
        -->
        <div v-if="pendingCard.gate_passed_at" class="d-flex align-center ga-1 text-caption text-medium-emphasis mb-2">
          <v-icon size="15">mdi-timer-sand</v-icon>
          平台检查已通过：只检查了代码规范和类型，未运行测试
        </div>
        <!-- 采纳 PR 化 (#188 §5.1): the real PR + its CI, live. -->
        <div v-if="pendingCard.pr_url" class="mb-2">
          <div class="d-flex align-center flex-wrap ga-2">
            <v-chip
              size="small"
              variant="tonal"
              prepend-icon="mdi-source-pull"
              :href="pendingCard.pr_url"
              target="_blank"
            >
              PR #{{ pendingCard.pr_number }}
            </v-chip>
            <span v-if="prChecks?.available && prChecks.mergeable === false" class="text-caption text-error">
              与主分支冲突
            </span>
          </div>
          <div
            v-for="chk in prChecks?.checks ?? []"
            :key="chk.name"
            class="d-flex align-center ga-1 text-caption text-medium-emphasis mt-1"
          >
            <v-icon
              size="14"
              :color="chk.conclusion === 'success' ? 'success' : chk.conclusion === 'failure' ? 'error' : undefined"
            >
              {{
                chk.conclusion === 'success'
                  ? 'mdi-check-circle'
                  : chk.conclusion === 'failure'
                    ? 'mdi-close-circle'
                    : 'mdi-progress-clock'
              }}
            </v-icon>
            {{ chk.name }}
            <span v-if="chk.status !== 'completed'">进行中</span>
          </div>
        </div>
        <!-- 主分支保护 (spec §4.4): N 人批准后采纳才会真正合入。 -->
        <div v-if="pendingCard.approvals_required > 1" class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-chip
            size="small"
            variant="tonal"
            :color="pendingCard.approvals.length >= pendingCard.approvals_required ? 'success' : undefined"
            prepend-icon="mdi-account-check-outline"
          >
            {{ pendingCard.approvals.length }}/{{ pendingCard.approvals_required }}
            已批准
          </v-chip>
          <span v-if="pendingCard.approvals.length" class="text-caption text-medium-emphasis">
            {{ pendingCard.approvals.map((h) => '@' + h).join('、') }}
          </span>
          <v-btn
            v-if="!pendingCard.approvals.includes(AUTHOR)"
            size="small"
            variant="outlined"
            class="btn-secondary"
            :disabled="acceptBusy"
            prepend-icon="mdi-thumb-up-outline"
            @click="onApproveCard"
          >
            批准
          </v-btn>
          <span v-else class="d-inline-flex align-center ga-1 text-caption text-medium-emphasis">
            <v-icon size="14">mdi-check</v-icon>你已批准
          </span>
        </div>
        <div class="d-flex align-center ga-2">
          <!-- 决策在聊天，审查在面板: the box asks for a decision, and the thing
               the decision is about is a diff in the panel next to it. Without
               this the reviewer had to guess which tab held it. -->
          <v-btn
            variant="outlined"
            class="btn-secondary"
            prepend-icon="mdi-file-search-outline"
            @click="emit('review')"
          >
            去验收
          </v-btn>
          <!-- 采纳 = 当场合并 (#718)：GitHub lane 亮在后端会合的那两档（clean /
               unstable），为什么灰写在 title 里；平台 lane 的采纳纯是人的判断，
               从不按状态灰。 -->
          <span :title="acceptBlockedTitle ?? undefined">
            <v-btn
              color="success"
              variant="flat"
              :loading="acceptBusy"
              :disabled="acceptBusy || !!acceptBlockedTitle"
              prepend-icon="mdi-check"
              @click="onAcceptCard"
            >
              {{ needsPr ? '创建 PR' : pendingCard.status === 'conflict' ? '重试采纳' : '采纳' }}
            </v-btn>
          </span>
          <v-btn
            variant="text"
            :disabled="acceptBusy"
            prepend-icon="mdi-undo"
            @click="showRejectInput = !showRejectInput"
          >
            退回
          </v-btn>
        </div>
        <!-- 绿了自动合 (#718)：项目允许、规则还没满足时才有；布防人由后端认定。 -->
        <div v-if="autoMergeVisible" class="d-flex align-center flex-wrap ga-2 mt-2">
          <v-switch
            :model-value="!!autoMergeArmedBy"
            color="success"
            density="compact"
            hide-details
            :disabled="acceptBusy"
            label="通过后自动合并"
            @update:model-value="onToggleAutoMerge"
          />
          <span v-if="autoMergeArmedBy" class="text-caption text-medium-emphasis">
            由 @{{ autoMergeArmedBy }} 开启
          </span>
        </div>
        <!--
          人工放行 (#718)：明知合并态不是 clean 仍合并。平台自己永远不走这条路——
          红着合有时候是对的（CI 抽风、与本次改动无关的既有失败），不能接受的是
          没有人做过这个决定。所以它默认收起、要填理由，点下去在卡上留名。
        -->
        <div v-if="pendingCard.pr_number && acceptBlockedTitle" class="mt-2">
          <v-btn
            v-if="!showForceMergeInput"
            size="small"
            variant="text"
            class="text-medium-emphasis"
            prepend-icon="mdi-alert-decagram-outline"
            @click="showForceMergeInput = true"
          >
            人工放行并合并
          </v-btn>
          <template v-else>
            <div class="text-caption text-medium-emphasis mb-1">
              在检查未全部通过的情况下强制合并。平台会记录操作人、时间和当时的检查状态。
            </div>
            <v-textarea
              v-model="forceMergeReason"
              label="理由"
              rows="2"
              auto-grow
              density="compact"
              variant="outlined"
              hide-details
              class="mb-2"
            />
            <div class="d-flex ga-2">
              <v-btn
                size="small"
                color="warning"
                variant="flat"
                :loading="acceptBusy"
                :disabled="acceptBusy"
                @click="onForceMerge"
              >
                确认放行并合并
              </v-btn>
              <v-btn size="small" variant="text" :disabled="acceptBusy" @click="showForceMergeInput = false">
                取消
              </v-btn>
            </div>
          </template>
        </div>
        <div v-if="showRejectInput" class="d-flex align-end ga-2 mt-3">
          <v-text-field
            v-model="rejectNote"
            variant="outlined"
            density="compact"
            hide-details
            placeholder="退回说明（可选）"
            class="flex-grow-1"
          />
          <v-btn variant="outlined" class="btn-secondary" :loading="acceptBusy" @click="onRejectCard"> 确认退回 </v-btn>
        </div>
      </div>
    </v-card>

    <!-- 已采纳等合并 (`pr_open`, #718 退役): 历史卡的兜底脸，参考闸门那两张的
         处理——只读、不转圈（转圈是在说平台此刻正跑着什么，而平台什么也没跑）。
         采纳现在当场合并，这个状态不会再有新卡进来。 -->
    <v-card v-else-if="deliveringCard" variant="outlined" class="merge-box mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="warning" size="19">mdi-history</v-icon>
          <span class="t-title">已采纳，未完成合并</span>
        </div>
        <div class="text-caption text-medium-emphasis mb-2">
          已由 <strong>@{{ deliveringCard.decided_by }}</strong> 采纳，但合并没有完成。这是一张旧卡，需要人工处理
        </div>
        <!-- 后端把故障写在卡的 note 上，这是它唯一露头的地方。轻重由后端下发的
             note_level 决定，不是从文案开头那个字符猜的 —— 所以这里画一个真的图
             标：颜色是唯一信号的话，色觉障碍和灰度截图上就什么都没有了。 -->
        <div
          v-if="deliveryNote"
          class="d-flex align-start ga-1 text-caption mb-2"
          :class="deliveryNote.tone === 'error' ? 'text-error' : 'text-medium-emphasis'"
        >
          <v-icon v-if="deliveryNote.tone === 'error'" icon="mdi-alert-circle-outline" size="14" class="mt-1" />
          <span>{{ deliveryNote.text }}</span>
        </div>
        <!-- PR + 实时 CI，复用待采纳卡那套 prChecks 轮询。 -->
        <div v-if="deliveringCard.pr_url">
          <div class="d-flex align-center flex-wrap ga-2">
            <v-chip
              size="small"
              variant="tonal"
              prepend-icon="mdi-source-pull"
              :href="deliveringCard.pr_url"
              target="_blank"
            >
              PR #{{ deliveringCard.pr_number }}
            </v-chip>
            <span v-if="deliveringCard.pr_head_sha" class="text-caption text-medium-emphasis">
              {{ deliveringCard.pr_head_sha.slice(0, 7) }}
            </span>
            <span v-if="prChecks?.available && prChecks.mergeable === false" class="text-caption text-error">
              与主分支冲突
            </span>
          </div>
          <div
            v-for="chk in prChecks?.checks ?? []"
            :key="chk.name"
            class="d-flex align-center ga-1 text-caption text-medium-emphasis mt-1"
          >
            <v-icon
              size="14"
              :color="chk.conclusion === 'success' ? 'success' : chk.conclusion === 'failure' ? 'error' : undefined"
            >
              {{
                chk.conclusion === 'success'
                  ? 'mdi-check-circle'
                  : chk.conclusion === 'failure'
                    ? 'mdi-close-circle'
                    : 'mdi-progress-clock'
              }}
            </v-icon>
            {{ chk.name }}
            <span v-if="chk.status !== 'completed'">进行中</span>
          </div>
        </div>
      </div>
    </v-card>

    <!-- Accepted topic: 采纳可撤销 (spec §6.3). -->
    <v-card v-else-if="acceptedCard" variant="outlined" class="merge-box mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
          <span class="t-title">已采纳</span>
        </div>
        <div class="text-body-2 c-muted mb-3">
          由 <strong>@{{ acceptedCard.decided_by }}</strong> 采纳
        </div>
        <v-btn
          variant="outlined"
          class="btn-secondary"
          :loading="acceptBusy"
          :disabled="acceptBusy"
          prepend-icon="mdi-undo"
          @click="onRevokeCard"
        >
          撤回采纳
        </v-btn>
      </div>
    </v-card>
  </template>
</template>

<style scoped>
/* 这一列里唯一的卡片，因为它是唯一的决策入口。绿色（合并的惯例色）保留，但
   强调改成边框而不是左竖条 —— ChatPanel 自己的规矩是「强调靠 wash 底色，不靠
   左竖条（左条纹只留给引用块和结构线）」，而这里原本就是一条 3px 左竖条。 */
.merge-box {
  position: relative;
  overflow: hidden;
  border-color: var(--ok) !important;
  border-radius: var(--radius-lg);
}
/* 机器闸门: tail of the failed check's output (查看输出). */
.gate-output {
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
/* Secondary button: 1px border, neutral text, surface bg. */
.btn-secondary {
  border: 1px solid var(--line-2);
  color: var(--text);
  background: var(--surface);
}
/* 「谁的活」的圈。形状和颜色都由 `lib/board.ts` 一处给出（内联样式），这里只管
   尺寸 —— scoped 样式进不了别的组件，颜色写在这儿就意味着卡和看板各有一份。 */
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
</style>
