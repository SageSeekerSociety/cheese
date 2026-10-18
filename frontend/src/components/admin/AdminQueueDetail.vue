<script setup lang="ts">
import type { AdminCandidate } from '@/api'
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminQueueDetailBody from '@/components/admin/AdminQueueDetailBody.vue'
import AdminQueueDetailSide from '@/components/admin/AdminQueueDetailSide.vue'
import BaseButton from '@/components/base/BaseButton.vue'

/**
 * AdminQueueDetail.vue — 一条反馈的详情（§4.4），三个断点共用这一个组件：
 *
 *   ≥1280  两栏：440px 左栏 + `minmax(0,1fr)` 右栏，64px 操作栏粘底
 *   <1280  一栏（右栏那 520px 抽屉里就是这个组件的窄形态）
 *   <768   一栏，操作栏照样粘底
 *
 * **三种形态是同一份模板 + 一条媒体查询**，不是三个组件。上一版把「宽屏那一栏」和
 * 「窄屏那个抽屉」写成了两套内容，于是详情里每加一个字段都要写两遍，而漏掉的那一遍
 * 在另一个断点上才看得见 —— 那种 bug 只有在别人换屏幕时才被抓到。
 *
 * **这一件现在只负责「版式」**：两栏怎么摆（含 1280 那个断点）、底下那根操作栏，以及
 * 把两栏报上来的意图原样转发出去。栏里的内容在两件里 —— 左栏的身份/分诊面板/时间线是
 * `AdminQueueDetailSide`，右栏的正文几段是 `AdminQueueDetailBody`。切法是「哪一栏」而
 * 不是「哪一段」，因为这一屏本来就在两栏之间分活儿，而栏里面的东西谁都不需要跨栏看。
 * 对外 props / emits 和 DOM 一个没变 —— 拆出来的两件各自是根节点直接落位，队列页、
 * 抽屉、e2e 里那些 `.qdet__*` 选择器看到的还是原来那棵树。
 *
 * **内容不在这里拉。** id 与防抖归调用方（F-06 的 150ms + `detailSeq` 在队列页），
 * 因为「手上这一条是谁」是页面级的判断（列表光标、抽屉开关都算进去），放在这一层会
 * 变成第二个「当前是哪条」。这里只画 `item`，以及在 `item` 为空时画加载 / 打不开。
 *
 * 写操作**全部从这里出去**，一件不落：**状态**是 `triage`（调用方要记撤销条，那是
 * 页面级的栈），**指派 / 优先级 / 安全问题 / 内部备注**各有自己的事件。这一层现在只认
 * props —— 它以前直接把后四样写进 store，于是「详情」和「业务 store」之间多了一条只有
 * 它俩知道的线，页面反而看不见自己这一屏发生了什么。
 *
 * 写失败的那句原话也是 prop（`writeError`）：它由调用方的 store 给，画在右栏里是因为
 * 「刚才那一下没成」要贴着刚点过的控件说，隔着一屏没人看得见。
 */
defineOptions({ name: 'AdminQueueDetail' })

const props = withDefaults(
  defineProps<{
    item: FeedbackDetail | null
    loading: boolean
    /** 拉取失败时的原话。为空表示这一趟没出错。 */
    error: string | null
    /** 分诊面板展开着没有。状态在调用方。 */
    triageOpen: boolean
    /** 服务端 meta 里那份状态梯子。缺省时用本地那一份。 */
    statuses?: FeedbackStatus[]
    /** 写操作失败的原话，由调用方的 store 给。 */
    writeError?: string
    /** 「指派给谁」那个框：候选与那串搜索词都归调用方（关详情时它得能清掉）。 */
    candidates?: AdminCandidate[]
    search?: string
    searching?: boolean
  }>(),
  { statuses: undefined, writeError: '', candidates: () => [], search: '', searching: false }
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
  (e: 'assign', id: string, handle: string | null): void
  (e: 'priority', id: string, priority: FeedbackPriority): void
  (e: 'security', id: string, security: boolean): void
  (e: 'note', id: string, body: string): void
  (e: 'search', v: string): void
  (e: 'reward', id: string): void
}>()

const { t } = useI18n()

/** 下一格。和 `AdminQueueRow` 的那张表同一份 —— 详情里那颗主按钮推的就是这一格。 */
const NEXT: Record<FeedbackStatus, FeedbackStatus | null> = {
  received: 'in_progress',
  in_progress: 'resolved',
  resolved: 'deployed',
  deployed: null,
  declined: null,
}

/** 按钮文案按**目标**状态取（和队列行同一个理由：按钮说出口的是它要做的那件事）。
 *  键名逐字来自 wave 0 的 `feedback.queue.advance.*`，两个地方共用同一批字。 */
const ADVANCE_LABEL: Record<FeedbackStatus, string> = {
  in_progress: 'feedback.queue.advance.inProgress',
  resolved: 'feedback.queue.advance.resolved',
  deployed: 'feedback.queue.advance.deployed',
  received: '',
  declined: '',
}

const next = computed(() => (props.item ? NEXT[props.item.status] : null))
const advanceLabel = computed(() => (next.value ? t(ADVANCE_LABEL[next.value]) : ''))

/** 备注草稿留在这一层，因为它和操作栏那颗「提交备注」是一件事的两半：字在右栏那个框里
 *  写，按钮在这一层，两边看的是同一份。 */
const noteDraft = ref('')

const sideRef = ref<InstanceType<typeof AdminQueueDetailSide> | null>(null)

/** 队列页的 `A` 键要落到的那个输入框。它现在长在左栏里，这一层只把它转出去 —— 对外还是
 *  一个 `focusAssignee`，调用方那行一行不用改。 */
function focusAssignee() {
  sideRef.value?.focusAssignee()
}

defineExpose({ focusAssignee })

function addNote() {
  const body = noteDraft.value.trim()
  if (!body || !props.item) return
  // 先清草稿再发：失败时用户手上那份字会没掉，但服务端回的是一句原话（调用方把它
  // 画在这一页上），而留在框里的话，他会以为「加进去了」再点一次，于是同一条备注
  // 写两遍 —— 备注是只增不改的流水，重复的那一条删不掉。
  noteDraft.value = ''
  emit('note', props.item.id, body)
}
</script>

<template>
  <div class="qdet">
    <div class="qdet__panes">
      <!-- 左栏。窄屏时它在上面，宽屏时它是左边那 440px。 -->
      <AdminQueueDetailSide
        ref="sideRef"
        :item="item"
        :triage-open="triageOpen"
        :statuses="statuses"
        :candidates="candidates"
        :search="search"
        :searching="searching"
        @close="emit('close')"
        @update:triage-open="emit('update:triageOpen', $event)"
        @triage="(to: FeedbackStatus) => emit('triage', to)"
        @assign="(id: string, handle: string | null) => emit('assign', id, handle)"
        @priority="(id: string, priority: FeedbackPriority) => emit('priority', id, priority)"
        @search="(v: string) => emit('search', v)"
      />

      <AdminQueueDetailBody
        v-model:note-draft="noteDraft"
        :item="item"
        :loading="loading"
        :error="error"
        :write-error="writeError"
        @security="(id: string, security: boolean) => emit('security', id, security)"
      />
    </div>

    <div v-if="item" class="qdet__reward">
      <p v-if="item.reward">{{ t('usage.offers.rewarded', { n: item.reward.credits }) }}</p>
      <template v-else-if="item.status !== 'declined'">
        <BaseButton kind="secondary" size="sm" @click="emit('reward', item.id)">{{
          t('usage.offers.reward')
        }}</BaseButton>
        <p>{{ t('usage.offers.rewardHint') }}</p>
      </template>
    </div>
    <div class="qdet__bar">
      <button v-if="next" type="button" class="qdet__primary" @click="emit('triage', next)">
        {{ advanceLabel }}
      </button>
      <span class="qdet__bar-spacer" />
      <button type="button" class="qdet__submit" :disabled="!noteDraft.trim()" @click="addNote">
        {{ t('feedback.queueDetail.submitNote') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.qdet__reward {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--surface);
  border-top: 1px solid var(--line);
}
.qdet__reward p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}
.qdet {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--canvas);
}

/* 窄屏这一档**不是** grid，就是一条普通往下滚的流：分诊那一摞在上、正文在下。
   曾经写成 `grid-template-rows: minmax(0,1fr)` 的单栏 grid，结果正文那一格
   （隐式的第二行，auto）把第一行挤成了 0 —— 返回 / 标题 / 状态 / 指派 / 优先级 /
   时间线整块看不见。宽屏那一档在下面那条媒体查询里改成 440 + 1fr 两栏。 */
.qdet__panes {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

/* 两栏各自的盒子写在**这里**、不写在各自那一件里：`.qdet__side` / `.qdet__main` 是不是
   grid 的两格、1280 以上两栏各自滚还是整屏滚，是「这一屏怎么摆」的一个决定（而 1280 那
   个数还得和 `AdminQueuePage` 里「宽屏整页 / 窄屏抽屉」那条一致）—— 分到两个子组件里就
   成了三处得同时改的数。`:deep()` 是因为这两个节点现在是子组件的根：根元素本来就带着父
   组件的 scope id，写出来只是让下一个人一眼看见这几条是**替别人画的盒子**。 */
.qdet__panes :deep(.qdet__side),
.qdet__panes :deep(.qdet__main) {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--surface);
}

/* 窄屏：左栏是上面那一段，分隔线画在它底下、整条横着。 */
.qdet__panes :deep(.qdet__side) {
  border-bottom: 1px solid var(--line);
}

/* 64px 操作栏（§4.4）。这一组里的琥珀是左边那颗主按钮（设计规范 §1.6）；
   右边那颗「提交备注」是次要动作，中性描边。底部内边距吃掉
   `env(safe-area-inset-bottom)`：窄屏上操作栏会压在 iOS 那条横条下面。 */
.qdet__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  height: 64px;
  padding: 0 16px;
  padding-bottom: env(safe-area-inset-bottom, 0);
  background: var(--surface);
  border-top: 1px solid var(--line);
}

/* 主操作，这一组里唯一的琥珀（设计规范 §1.6）。字色走 `--v-theme-on-primary`，和全站每一颗
   琥珀填充按钮同一处取值（见 `MyDevicesView` 那段豁免说明：浅色下它对 #F57F17
   推出来的就是白字，2.65:1，是 2026-08-16 拍板保留的已知代价，别在这里「修好」）。 */
.qdet__primary {
  height: 40px;
  padding: 0 16px;
  color: rgb(var(--v-theme-on-primary));
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  cursor: pointer;
  background: var(--accent);
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}

.qdet__primary:hover {
  background: var(--accent-press);
}

.qdet__bar-spacer {
  flex: 1 1 auto;
}

.qdet__submit {
  height: 40px;
  padding: 0 16px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}

.qdet__submit:hover:not(:disabled) {
  background: var(--fill);
}

.qdet__submit:disabled {
  color: var(--muted);
  cursor: default;
}

/* 宽屏：§4.4 的左 440 + 右 `minmax(0,1fr)`。断点是 1280 —— 下面这条和
   `AdminQueuePage` 里那条决策（宽屏用这一页、窄屏用抽屉）必须是同一个数，改一处
   就要改两处。 */
@media (min-width: 1280px) {
  .qdet__panes {
    display: grid;
    grid-template-columns: 440px minmax(0, 1fr);
    /* 两栏各自滚，所以这里把窄屏那层滚动脱掉。 */
    overflow: visible;
  }

  .qdet__panes :deep(.qdet__side),
  .qdet__panes :deep(.qdet__main) {
    min-height: 0;
    overflow-y: auto;
  }

  /* 左栏按内容高、不撑满整列：内容短的时候下面留的是页面底（`--canvas`），
     而不是一大块空着的白盒子。分隔线因此挪到正文那一栏的左边，才还是整列高的
     一条 —— 左栏自己那点高度上画不出来。 */
  .qdet__panes :deep(.qdet__side) {
    align-self: start;
    max-height: 100%;
    border-bottom: 0;
  }

  .qdet__panes :deep(.qdet__main) {
    border-left: 1px solid var(--line);
  }
}
</style>
