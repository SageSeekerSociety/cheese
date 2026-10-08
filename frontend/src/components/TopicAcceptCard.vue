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
// The data (the card list, the PR-checks poll) lives in `useAcceptCard`. A page
// that shows the card in more than one place takes one copy with
// `provideAcceptCard` and every box on it reads that copy; rendered on its own,
// the box takes its own. TopicView only ever says 「重新拉一次」 (`reload`),
// which it does when 芝士 files a card or a `cheese` command changes one mid-turn.
//
// 这一件现在只剩**接线**：判断与动作在 `composables/useAcceptCard.ts`，几张脸的
// 画法在 `components/accept/*.vue`，谁点哪一下打哪个动作看下面那个模板就够了。
// 拆开之前它是一块 1215 行的模板（#2143）。
//
// 待审阅的卡在这里只剩一条（AcceptDockBar）：状态，加上轮到人时的「退回 / 采纳」。
// 交的是什么、检查怎样、审阅重点、改派和作废都在「改动」页顶部（ChangesReviewHead），
// 审阅本来就在那边看。
import type { CardPhase } from '@/lib/topicState'

import { watch } from 'vue'

import { injectAcceptCard, useAcceptCard } from '@/composables/useAcceptCard'

import AcceptDeliveringFace from '@/components/accept/AcceptDeliveringFace.vue'
import AcceptDockBar from '@/components/accept/AcceptDockBar.vue'
import AcceptGateFace from '@/components/accept/AcceptGateFace.vue'
import AcceptRejectForm from '@/components/accept/AcceptRejectForm.vue'

const props = defineProps<{
  topicId: string
  topicStatus: string
  taskId?: string | null
  /** 手机上对话和「改动」是两个页签：这一条只放「审阅」，决定在「改动」页底部。 */
  reviewButton?: boolean
}>()
const emit = defineEmits<{
  (e: 'phase', phase: CardPhase): void
  /** 去审阅: show me what I am being asked to accept. */
  (e: 'review'): void
  /** 正在写退回理由：这段时间输入框让给退回那一块。 */
  (e: 'rejecting', on: boolean): void
}>()

// 页面已经取了一份（provideAcceptCard）就读那一份：对话栏、面板底部、「改动」页顶部
// 看的是同一张卡的同一刻。单独渲染时（预览站、测试）自己取。
const card = injectAcceptCard() ?? useAcceptCard(props)
const {
  acceptBusy,
  expanded,
  hasBox,
  bar,
  barColumn,
  decisionOpen,
  acceptLabel,
  phase,
  loaded,
  animate,
  pendingCard,
  acceptedCard,
  deliveringCard,
  gateCard,
  acceptBlockedTitle,
  deliveryNote,
  prChecks,
  showGateOutput,
  showRejectInput,
  rejectNote,
  onAcceptCard,
  onRevokeCard,
  onRejectCard,
  agentName,
  agentHandle,
} = card

// 决策在聊天，审查在面板: where the topic stands is not this box's private business.
// The header states it and the panel opens on the tab it calls for, so the one word
// travels up rather than the card list travelling out.
//
// It is reported only once the cards are actually in: before that, 「没有卡」 and
// 「卡还没拉回来」 look identical from outside, and the panel would open on 文档
// for a topic that was waiting to be reviewed.
watch([loaded, phase], () => {
  if (loaded.value) emit('phase', phase.value)
})

const rejecting = () => !!pendingCard.value && showRejectInput.value
watch(rejecting, (on) => emit('rejecting', on), { immediate: true })

function cancelReject() {
  showRejectInput.value = false
}

defineExpose({ reload: card.reload })
</script>

<template>
  <!-- 卡出现 / 收走是房间里真发生的一件事，要看得出是这里多了一块（折叠）。
       打开房间时读到的那一张本来就在，不演：`animate` 只在第一次读完之后才打开。 -->
  <Transition name="accept-fold" :css="animate">
    <div v-if="hasBox" class="accept-fold">
      <div class="accept-fold__inner">
        <div class="accept-dock">
          <!-- 历史卡（闸门那两张、交付中那张）点横条才在它上面展开当年那张卡。 -->
          <Transition name="accept-fold">
            <div v-if="expanded && (gateCard || deliveringCard)" class="accept-fold">
              <div class="accept-fold__inner">
                <div id="accept-detail" class="accept-dock__detail">
                  <AcceptGateFace
                    v-if="gateCard"
                    :card="gateCard"
                    :open="showGateOutput"
                    :agent-name="agentName"
                    :agent-handle="agentHandle"
                    @update:open="showGateOutput = $event"
                  />
                  <!-- 已采纳等合并 (`pr_open`, #718 退役): 历史卡的兜底脸，只读、不转圈。 -->
                  <AcceptDeliveringFace
                    v-else-if="deliveringCard"
                    :card="deliveringCard"
                    :checks="prChecks"
                    :note="deliveryNote"
                  />
                </div>
              </div>
            </div>
          </Transition>

          <AcceptRejectForm
            v-if="pendingCard && showRejectInput"
            v-model:note="rejectNote"
            :busy="acceptBusy"
            @cancel="cancelReject"
            @confirm="onRejectCard"
          />
          <AcceptDockBar
            v-else
            :title="bar.title"
            :icon="bar.icon"
            :color="bar.color"
            :column="gateCard ? null : barColumn"
            :decide="!gateCard && decisionOpen"
            :accept-label="acceptLabel"
            :blocked-title="acceptBlockedTitle"
            :busy="acceptBusy"
            :review-button="!!pendingCard && !gateCard && reviewButton"
            :revoke="!pendingCard && !gateCard && !deliveringCard && !!acceptedCard"
            :expandable="!!(gateCard || deliveringCard)"
            :expanded="expanded"
            @review="emit('review')"
            @toggle="expanded = !expanded"
            @accept="onAcceptCard"
            @reject="showRejectInput = true"
            @revoke="onRevokeCard"
          />
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.accept-fold {
  display: grid;
  /* 列宽的下限不能是内容的最小宽度：卡里一个没有断点的长串（下划线连起来的标识符）
     会把整张卡撑得比对话栏宽，右边被裁掉。 */
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: 1fr;
}
.accept-fold__inner {
  min-width: 0;
  min-height: 0;
}
/* 进来走 --dur-base 的减速，走掉快一档、加速离开（§9.3）。 */
.accept-fold-enter-active {
  transition:
    grid-template-rows var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.accept-fold-leave-active {
  transition:
    grid-template-rows var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
/* 只在演的那一下里裁：平时裁的话，卡边上按钮的焦点环会被切掉一半。 */
.accept-fold-enter-active .accept-fold__inner,
.accept-fold-leave-active .accept-fold__inner {
  overflow: hidden;
}
.accept-fold-enter-from,
.accept-fold-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}
/* 贴着输入框的那一块：一条边框把它和对话分开，底色和对话栏一样。展开的历史卡有上
   限，再长就在里面滚：它不能把对话整个盖住。 */
/* 左右缩进和输入框里那一圈内边距（RoomComposer 的 12px）对齐；外层自己已经缩进的
   地方（演示页）把它设成 0。 */
.accept-dock {
  margin: 0 var(--accept-dock-inset, 12px) 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.accept-dock__detail {
  /* 按看得见的那一截算：手机上键盘弹起来时，50vh 会把输入框顶到屏幕外。 */
  max-height: calc((var(--app-height, 100dvh) - var(--keyboard-inset, 0px)) * 0.5);
  overflow-y: auto;
  border-bottom: 1px solid var(--line);
}
</style>
