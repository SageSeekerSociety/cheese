<script setup lang="ts">
// 成果待采纳框 (eval C5/A3): the box at the end of the conversation timeline,
// GitHub's merge box in shape. It has five mutually exclusive faces — 闸门运行中
// / 闸门未通过 / 闸门未能执行 / 待采纳 / 交付中 / 已采纳 — and each of them says a
// different thing about who is waiting on whom.
//
// It owns its own data (the card list, the gate poll, the PR-checks poll) rather
// than taking them as props: everything here is about this one topic's cards and
// nothing outside needs to read them. TopicView only ever says 「重新拉一次」
// (`reload`), which it does when 芝士 files a card or a `cheese` command changes
// one mid-turn.
import type { AcceptCard, PrChecks } from '@/cx_types'

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
} from '@/api'
import { deliveryNoteTone, deliveryStageOf } from '@/lib/deliveryStage'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ topicId: string; topicStatus: string }>()
const store = useWorkspaceStore()
const AUTHOR = myHandle()

const acceptCards = ref<AcceptCard[]>([])
const acceptBusy = ref(false)
const rejectNote = ref('')
const showRejectInput = ref(false)
// 人工放行 (App 采纳等 CI 再合): 明知检查没全绿仍合并。默认拒绝、显式放行，所以
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

// 交付进度 (两阶段采纳, 2026-08-09): the human already clicked 采纳 and the PR is
// open — CI and the merge run for hours after that. Without this branch the whole
// merge box vanishes the moment someone accepts, and nothing on screen says the
// delivery is still in flight. Read-only: the decision was already made, nobody
// should be asked to click a second time.
const deliveringCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'pr_open') ?? null)
// 步骤由后端下发（`card.stages`）——哪些步骤存在取决于项目的 forge，浏览器看不见。
// 这里只把它们配上文案，见 lib/deliveryStage.ts。
const deliveryStage = computed(() => (deliveringCard.value ? deliveryStageOf(deliveringCard.value) : null))
// 交付途中后端把阶段信息/故障写在卡的 note 上（CI 红了、GitHub 拒绝合并、轮询用的
// token 失效），那是这些事唯一露头的地方，照原样显示。
const deliveryNote = computed(() => {
  const note = deliveringCard.value?.note ?? ''
  const tone = deliveryNoteTone(note)
  return tone ? { text: note, tone } : null
})

// 机器闸门 (eval C2): the newest card while the platform check runs / after it
// failed. Only the newest card can be in a gate state (one in-flight card per
// topic is enforced server-side).
const gateCard = computed<AcceptCard | null>(() => {
  const c = acceptCards.value[0]
  return c && (c.status === 'pending_gate' || c.status === 'gate_failed' || c.status === 'gate_blocked') ? c : null
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
  // silent = a background refresh (gate polling / after a vote): keep the
  // current cards on screen instead of blanking the box for a beat.
  if (!silent) {
    acceptCards.value = []
    showRejectInput.value = false
    rejectNote.value = ''
    showGateOutput.value = false
  }
  const tid = props.topicId
  if (!tid) return
  try {
    const payload = await getAcceptCards(tid)
    if (props.topicId === tid) acceptCards.value = payload.data
  } catch {
    // Best-effort; the banner just stays hidden.
  }
}

// While the check runs (it can take minutes), poll the card until it settles.
let gatePollTimer: number | null = null
watch(
  () => gateCard.value?.status === 'pending_gate',
  (running) => {
    if (running && gatePollTimer === null) {
      gatePollTimer = window.setInterval(() => loadAcceptCard(true), 2500)
    } else if (!running && gatePollTimer !== null) {
      window.clearInterval(gatePollTimer)
      gatePollTimer = null
    }
  }
)
onUnmounted(() => {
  if (gatePollTimer !== null) window.clearInterval(gatePollTimer)
})

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
        // 交付中卡本身也在变（合并时间、note、最终 accepted），跟着一起刷新，否则
        // 界面会停在采纳那一刻的快照上直到用户手动切话题。
        if (deliveringCard.value) void loadAcceptCard(true)
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
    const updated = await acceptCard(card.id, AUTHOR)
    if (updated.status === 'conflict') {
      store.error = '合并冲突，这次没有归档——芝士已被派去解决，它汇报后再点「重试采纳」。'
    }
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '采纳失败')
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
  const card = deliveringCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await mergeCardAnyway(card.id, forceMergeReason.value)
    showForceMergeInput.value = false
    forceMergeReason.value = ''
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '人工放行失败')
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

defineExpose({ reload: loadAcceptCard })
</script>

<template>
  <template v-if="hasBox">
    <!-- 机器闸门 (eval C2): the platform is running the project's 质量检查 in this
         topic's workspace — the card reaches the reviewer only when it's green. -->
    <v-card v-if="gateCard && gateCard.status === 'pending_gate'" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-progress-circular indeterminate size="18" width="2" />
          <span class="t-title">平台检查进行中…</span>
        </div>
        <div class="text-caption text-medium-emphasis">
          正在这个话题的工作区里运行项目配置的质量检查，通过后验收卡才会送给
          <strong>@{{ gateCard.reviewer_handle }}</strong
          >。
        </div>
      </div>
    </v-card>

    <!-- 闸门未过：卡片作废，芝士已被通知去修，修完会重新递卡。 -->
    <v-card v-else-if="gateCard && gateCard.status === 'gate_failed'" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
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
        <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '（无输出）' }}</pre>
      </div>
    </v-card>

    <!-- 闸门没跑成：检查本身没能在门禁容器里跑起来，对代码没有结论。刻意跟
         「未通过」分开显示——它是需要人看一眼的状态，不是代码红了。 -->
    <v-card v-else-if="gateCard && gateCard.status === 'gate_blocked'" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="warning" size="19">mdi-help-circle-outline</v-icon>
          <span class="t-title">平台检查未能执行</span>
        </div>
        <div class="text-caption text-medium-emphasis mb-2">
          检查程序没能启动，所以它对这次改动<strong>没有结论</strong>（既不是通过也不是未通过）。
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
        <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '（无输出）' }}</pre>
      </div>
    </v-card>

    <v-card v-else-if="pendingCard" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
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
          {{ pendingCard.note || '采纳时发生合并冲突，芝士正在工作区里解决。' }}
          它在对话里汇报完成后即可重试。
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
          机器闸门 (eval C2) + 人类授权动作前移 (2026-08-10): 闸门跑的是
          check.sh --no-tests——lint 和类型，没有测试。真 CI 只在 PR 上跑，
          而 PR 是你点下去之后才开的。所以这一格绝不能是绿勾：那等于让卡面
          替一段还没被任何测试碰过的代码背书。它说的是"即将开始跑"。
        -->
        <div v-if="pendingCard.gate_passed_at" class="d-flex align-center ga-1 text-caption text-medium-emphasis mb-2">
          <v-icon size="15">mdi-timer-sand</v-icon>
          平台检查已通过（只跑了 lint 和类型检查，没有跑测试）· 完整 CI 在你授权后才开始
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
            <span v-if="chk.status !== 'completed'">（进行中）</span>
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
          <v-btn
            color="success"
            variant="flat"
            :loading="acceptBusy"
            :disabled="acceptBusy"
            prepend-icon="mdi-check"
            @click="onAcceptCard"
          >
            {{ pendingCard.status === 'conflict' ? '重试采纳' : '采纳并归档' }}
          </v-btn>
          <v-btn
            variant="text"
            :disabled="acceptBusy"
            prepend-icon="mdi-undo"
            @click="showRejectInput = !showRejectInput"
          >
            退回
          </v-btn>
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

    <!-- 交付进度: 人已经点过采纳，剩下的（检查、合并——具体几步由项目的 forge
         决定，后端下发）是机器在跑，要跑几小时。只读，不给任何按钮 —— 授权已经
         给过了，不该再问人第二次。 -->
    <v-card v-else-if="deliveringCard" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-progress-circular indeterminate size="18" width="2" />
          <span class="t-title">交付中 · {{ deliveryStage?.title }}</span>
        </div>
        <div class="text-caption text-medium-emphasis mb-2">
          已由 <strong>@{{ deliveringCard.decided_by }}</strong> 采纳，{{ deliveryStage?.hint }}
        </div>
        <!-- 阶段条：人点完之后走到哪一步了 -->
        <div class="d-flex align-center flex-wrap ga-1 text-caption mb-2">
          <template v-for="(step, i) in deliveryStage?.steps ?? []" :key="step.key">
            <v-icon v-if="i > 0" size="13" class="text-disabled">mdi-chevron-right</v-icon>
            <span
              class="d-flex align-center ga-1"
              :class="step.state === 'todo' ? 'text-disabled' : 'text-medium-emphasis'"
            >
              <v-progress-circular v-if="step.state === 'active'" indeterminate size="13" width="2" />
              <v-icon v-else-if="step.state === 'done'" color="success" size="14">mdi-check-circle</v-icon>
              <v-icon v-else size="14">mdi-circle-outline</v-icon>
              {{ step.label }}
            </span>
          </template>
        </div>
        <!-- 后端把故障写在卡的 note 上，这是它唯一露头的地方。 -->
        <div
          v-if="deliveryNote"
          class="text-caption mb-2"
          :class="deliveryNote.tone === 'error' ? 'text-error' : 'text-medium-emphasis'"
        >
          {{ deliveryNote.text }}
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
            <span v-if="chk.status !== 'completed'">（进行中）</span>
          </div>
        </div>
        <!--
          人工放行：明知检查没全绿仍合并。平台自己永远不走这条路——红着合
          有时候是对的（CI 抽风、与本次改动无关的既有失败），不能接受的是
          没有人做过这个决定。所以它默认收起、要填理由，点下去在卡上留名。
        -->
        <div class="mt-3">
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
      </div>
    </v-card>

    <!-- Archived (accepted) topic: 采纳可撤销 (spec §6.3). -->
    <v-card v-else-if="acceptedCard" variant="outlined" class="merge-box mt-2">
      <div class="merge-box__bar" />
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
          <span class="t-title">已采纳并归档</span>
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
/* GitHub-PR-style merge box — green (the merge convention) stays. */
.merge-box {
  position: relative;
  overflow: hidden;
  border-color: var(--ok) !important;
}
.merge-box__bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--ok);
}
/* 机器闸门: tail of the failed check's output (查看输出). */
.gate-output {
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 6px;
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
</style>
