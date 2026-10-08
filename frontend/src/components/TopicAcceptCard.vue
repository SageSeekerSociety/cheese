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
//
// 这一件现在只剩**接线**：判断与动作在 `composables/useAcceptCard.ts`，五张脸的
// 画法在 `components/accept/*.vue`，谁点哪一下打哪个动作看下面那个模板就够了。
// 拆开之前它是一块 1215 行的模板（#2143）。
import type { CardPhase } from '@/lib/topicState'

import { watch } from 'vue'

import { useAcceptCard } from '@/composables/useAcceptCard'

import AcceptDecidedFace from '@/components/accept/AcceptDecidedFace.vue'
import AcceptDeliveringFace from '@/components/accept/AcceptDeliveringFace.vue'
import AcceptDockBar from '@/components/accept/AcceptDockBar.vue'
import AcceptGateFace from '@/components/accept/AcceptGateFace.vue'
import AcceptPendingFace from '@/components/accept/AcceptPendingFace.vue'

const props = defineProps<{
  topicId: string
  topicStatus: string
  taskId?: string | null
  /** 贴在对话栏输入框上方：平时只剩一行横条，点开才是整张卡。不贴底的时候（任务卡
   *  详情里）整张卡照旧摊开。 */
  docked?: boolean
}>()
const emit = defineEmits<{
  (e: 'phase', phase: CardPhase): void
  /** 去验收: show me what I am being asked to accept. */
  (e: 'review'): void
}>()

const {
  acceptBusy,
  expanded,
  showDetail,
  hasBox,
  bar,
  phase,
  loaded,
  animate,
  // 台面上的那几张卡
  pendingCard,
  acceptedCard,
  deliveringCard,
  gateCard,
  // 待采纳那张脸要读的
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
  reload,
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
  author,
  agentName,
  agentHandle,
} = useAcceptCard(props)

// 决策在聊天，审查在面板: the card stays here — accepting is a social decision
// and needs the conversation around it — but where the topic stands is not this
// box's private business. The header states it and the panel opens on the tab it
// calls for, so the one word travels up rather than the card list travelling out.
//
// It is reported only once the cards are actually in: before that, 「没有卡」 and
// 「卡还没拉回来」 look identical from outside, and the panel would open on 文档
// for a topic that was waiting to be reviewed.
watch([loaded, phase], () => {
  if (loaded.value) emit('phase', phase.value)
})

defineExpose({ reload })
</script>

<template>
  <!-- 卡出现 / 收走是房间里真发生的一件事，要看得出是这里多了一块（折叠）。
       打开房间时读到的那一张本来就在，不演：`animate` 只在第一次读完之后才打开。 -->
  <Transition name="accept-fold" :css="animate">
    <div v-if="hasBox" class="accept-fold">
      <div class="accept-fold__inner">
        <!-- 贴在输入框上方的一条：平时只有一行（这是什么、等谁、去验收），点开才
             在它上面展开整张卡。它原来是对话末尾一张 280px 高的卡，一递上来对话就
             只剩几行；而它说的是「有一个决定在等人」，这件事一行就说得完。 -->
        <div class="accept-dock" :class="{ 'accept-dock--docked': docked }">
          <!-- 点横条展开 / 收起，和整张卡进出同一个折叠：展开的那一块是从横条上长
               出来的，不是凭空跳出来一张卡。 -->
          <Transition name="accept-fold">
            <div v-if="showDetail" class="accept-fold">
              <div class="accept-fold__inner">
                <div id="accept-detail" class="accept-dock__detail">
                  <!-- 不贴底的时候没有横条，卡自己的标题行就在这里。 -->
                  <div v-if="!docked" class="accept-head">
                    <v-icon :color="bar.color" size="19">{{ bar.icon }}</v-icon>
                    <span class="t-title">{{ bar.title }}</span>
                  </div>

                  <!-- 闸门未过 / 闸门没跑成：历史卡的两张只读脸。 -->
                  <AcceptGateFace
                    v-if="gateCard"
                    :card="gateCard"
                    :open="showGateOutput"
                    :agent-name="agentName"
                    :agent-handle="agentHandle"
                    @update:open="showGateOutput = $event"
                  />

                  <AcceptPendingFace
                    v-else-if="pendingCard"
                    v-model:show-reject-input="showRejectInput"
                    v-model:reject-note="rejectNote"
                    v-model:show-void-input="showVoidInput"
                    v-model:void-note="voidNote"
                    v-model:show-force-merge-input="showForceMergeInput"
                    v-model:force-merge-reason="forceMergeReason"
                    :card="pendingCard"
                    :badge="mergeBadge"
                    :reasons="mergeReasons"
                    :forge-declaration="forgeDeclaration"
                    :note="pendingNote"
                    :reviewer-choices="reviewerChoices"
                    :busy="acceptBusy"
                    :blocked-title="acceptBlockedTitle"
                    :needs-pr="needsPr"
                    :auto-merge-visible="autoMergeVisible"
                    :auto-merge-armed-by="autoMergeArmedBy"
                    :pr-checks="prChecks"
                    :docked="!!docked"
                    :my-handle="author"
                    :agent-name="agentName"
                    :agent-handle="agentHandle"
                    :deliverable-busy="deliverableBusy"
                    :deliverable-error="deliverableError"
                    @accept="onAcceptCard"
                    @review="emit('review')"
                    @approve="onApproveCard"
                    @reassign="onReassignCard"
                    @download="onDownloadDeliverable"
                    @reject="onRejectCard"
                    @void="onVoidCard"
                    @force-merge="onForceMerge"
                    @toggle-auto-merge="onToggleAutoMerge"
                  />

                  <!-- 已采纳等合并 (`pr_open`, #718 退役): 历史卡的兜底脸，只读、不转圈。 -->
                  <AcceptDeliveringFace
                    v-else-if="deliveringCard"
                    :card="deliveringCard"
                    :checks="prChecks"
                    :note="deliveryNote"
                  />

                  <!-- Accepted topic: 采纳可撤销 (spec §6.3). -->
                  <AcceptDecidedFace
                    v-else-if="acceptedCard"
                    :card="acceptedCard"
                    :busy="acceptBusy"
                    @revoke="onRevokeCard"
                  />
                </div>
              </div>
            </div>
          </Transition>

          <AcceptDockBar
            v-if="docked"
            :icon="bar.icon"
            :color="bar.color"
            :title="bar.title"
            :sub="bar.sub"
            :expanded="expanded"
            :can-review="!!pendingCard"
            @toggle="expanded = !expanded"
            @review="emit('review')"
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
/* 贴着输入框的那一块：一条顶线把它和对话分开，底色和对话栏一样。展开的详情有上
   限，再长就在里面滚——它不能把对话整个盖住。 */
.accept-dock {
  margin-top: 8px;
  border: 1px solid var(--ok);
  border-radius: var(--radius-lg);
}
.accept-dock--docked {
  margin: 0 12px 8px;
  border-color: var(--line);
  background: var(--surface);
}
.accept-dock--docked .accept-dock__detail {
  /* 按看得见的那一截算：手机上键盘弹起来时，50vh 会把输入框顶到屏幕外。 */
  max-height: calc((var(--app-height, 100dvh) - var(--keyboard-inset, 0px)) * 0.5);
  overflow-y: auto;
  border-bottom: 1px solid var(--line);
}
.accept-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 0;
}
</style>
