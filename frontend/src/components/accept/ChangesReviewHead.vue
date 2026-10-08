<script setup lang="ts">
// 「改动」页顶部：这次交付现在怎样。它随改动一起往下滚，看文件时不占地方。
//
// 只有三样：标题（右边是 PR 链接和「更多操作」），一排信号，以及交付的人写的审阅重点。
// 决定按钮不在这里：并排时在对话栏的那一条上，铺满时在面板底部，同一时刻只有一份。
//
// 信号逐个从卡上的数据来，没有数据的那一个就不出现：托管方不报告检查就没有「检查」，
// 交付不是合并就没有「合并」，只要一个人批准就没有「批准」。点一个信号，在这一排下面
// 展开它的明细（哪几项检查没过、为什么现在合不了、谁已经批准）。
//
// 它只画，不知道卡是从哪来的：状态词、依据、可选的审阅人都是算好递进来的，每个操作
// 只往外报一个意图，打哪个端点由 `composables/useAcceptCard.ts` 决定。
import type { AcceptCard, MergeReason, PrChecks, ProjectMemberRow } from '@/cx_types'
import type { MergeBadge } from '@/lib/mergeState'
import type { MenuAction } from '../common/menuAction'

import { computed, ref } from 'vue'

import AdaptiveDialog from '../common/AdaptiveDialog.vue'
import AdaptiveMenu from '../common/AdaptiveMenu.vue'

import AcceptNoteLine from './AcceptNoteLine.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { columnDotStyle } from '@/lib/board'

const props = defineProps<{
  card: AcceptCard
  /** 合并态的词 + 圈；冲突卡没有（下面那句已经说了芝士在处理）。 */
  badge: MergeBadge | null
  reasons: MergeReason[]
  forgeDeclaration: string
  note: { text: string; tone: 'error' | 'info' } | null
  prChecks: PrChecks | null
  reviewerChoices: ProjectMemberRow[]
  busy: boolean
  autoMergeVisible: boolean
  autoMergeArmedBy: string | null
  forceMergeVisible: boolean
  /** 我是谁：「你已批准」按它算。 */
  myHandle: string
  agentName: string
  agentHandle: string | null
  deliverableBusy: boolean
  deliverableError: string
}>()

const emit = defineEmits<{
  (e: 'approve'): void
  (e: 'reassign', handle: string): void
  (e: 'download'): void
  (e: 'void'): void
  (e: 'force-merge'): void
  (e: 'toggle-auto-merge', enabled: boolean): void
}>()

const showVoidInput = defineModel<boolean>('showVoidInput', { required: true })
const voidNote = defineModel<string>('voidNote', { required: true })
const showForceMergeInput = defineModel<boolean>('showForceMergeInput', { required: true })
const forceMergeReason = defineModel<string>('forceMergeReason', { required: true })

// 标题按被审阅的东西实际是什么说：交一次合并时是这次改动自己的标题（合并后的提交
// 标题就是它）；交文件、交地址时是产物的名字和第几版。
const title = computed(() => {
  const c = props.card
  if (c.deliverable?.kind === 'merge' && c.change_subject) return c.change_subject
  if (c.artifact) return t('work.room.accept.artifact', { name: c.artifact.name, version: c.artifact.version })
  return c.change_subject || t('work.room.accept.change')
})

const focusItems = computed(() =>
  props.card.focus
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
)

type SignalKey = 'checks' | 'merge' | 'approvals'
interface Signal {
  key: SignalKey
  label: string
  /** 图标和它的颜色（mark 色）；没有图标时画「该谁动」的圈。 */
  icon?: string
  iconColor?: string
  dot?: Record<string, string>
  /** 有没有明细可展开。 */
  more: boolean
}

const FAILED = ['failure', 'cancelled', 'timed_out', 'action_required']
const checkRows = computed(() => {
  const rows = props.card.pr_number && props.prChecks?.available ? props.prChecks.checks ?? [] : []
  // 没过的排前面：展开明细时人要找的是它们。
  const rank = (c: (typeof rows)[number]) =>
    FAILED.includes(c.conclusion ?? '') ? 0 : c.status !== 'completed' ? 1 : 2
  return [...rows].sort((a, b) => rank(a) - rank(b))
})
const failedChecks = computed(() => checkRows.value.filter((c) => FAILED.includes(c.conclusion ?? '')).length)
const runningChecks = computed(() => checkRows.value.filter((c) => c.status !== 'completed').length)

const signals = computed<Signal[]>(() => {
  const out: Signal[] = []
  const n = checkRows.value.length
  if (n) {
    if (failedChecks.value)
      out.push({
        key: 'checks',
        label: t('work.room.review.checksFailed', { n: failedChecks.value }),
        icon: 'mdi-close-circle',
        iconColor: 'var(--danger)',
        more: true,
      })
    else if (runningChecks.value)
      out.push({
        key: 'checks',
        label: t('work.room.review.checksRunning', { n: runningChecks.value }),
        icon: 'mdi-progress-clock',
        iconColor: 'var(--faint)',
        more: true,
      })
    else
      out.push({
        key: 'checks',
        label: t('work.room.review.checksPassed', { n }),
        icon: 'mdi-check-circle-outline',
        iconColor: 'var(--ok)',
        more: true,
      })
  }
  const c = props.card
  // 交的是一份文件或一个地址时没有合并这回事；其余（一次合并，或者产物清单落地之前
  // 递的老卡）都要合进去。
  if (props.badge && c.deliverable?.kind !== 'file' && c.deliverable?.kind !== 'link') {
    const clean = c.merge_state.state === 'clean'
    out.push({
      key: 'merge',
      label: props.badge.label,
      icon: clean ? 'mdi-source-merge' : undefined,
      iconColor: clean ? 'var(--ok)' : undefined,
      dot: clean ? undefined : columnDotStyle(props.badge.column),
      more: props.reasons.length > 0 || !!props.forgeDeclaration,
    })
  }
  if (c.approvals_required > 1) {
    const done = c.approvals.length >= c.approvals_required
    out.push({
      key: 'approvals',
      label: t('work.room.accept.approvedCount', { done: c.approvals.length, total: c.approvals_required }),
      icon: done ? 'mdi-account-check' : 'mdi-account-check-outline',
      iconColor: done ? 'var(--ok)' : 'var(--faint)',
      more: true,
    })
  }
  return out
})

// 同一时刻只展开一个信号的明细。
const openSignal = ref<SignalKey | null>(null)
function toggleSignal(s: Signal) {
  if (!s.more) return
  openSignal.value = openSignal.value === s.key ? null : s.key
}

const reassignOpen = ref(false)
function reassign(handle: string) {
  reassignOpen.value = false
  emit('reassign', handle)
}

// 偶尔才用、或者有后果的那几样收在「更多操作」里：改派、自动合并、明知没过仍要采纳、作废。
const moreActions = computed<MenuAction[]>(() => {
  const list: MenuAction[] = [
    {
      key: 'reassign',
      label: t('work.room.review.reassign'),
      icon: 'mdi-account-switch-outline',
      disabled: props.busy,
      onSelect: () => (reassignOpen.value = true),
    },
  ]
  if (props.autoMergeVisible) {
    const armed = !!props.autoMergeArmedBy
    list.push({
      key: 'auto-merge',
      label: armed ? t('work.room.review.autoMergeOff') : t('work.room.accept.autoMerge'),
      icon: armed ? 'mdi-timer-off-outline' : 'mdi-timer-check-outline',
      disabled: props.busy,
      onSelect: () => emit('toggle-auto-merge', !armed),
    })
  }
  if (props.forceMergeVisible) {
    list.push({
      key: 'force-merge',
      label: t('work.room.accept.forceMerge'),
      icon: 'mdi-alert-decagram-outline',
      disabled: props.busy,
      onSelect: () => (showForceMergeInput.value = true),
    })
  }
  list.push({
    key: 'void',
    label: t('work.room.accept.void'),
    icon: 'mdi-close-circle-outline',
    danger: true,
    disabled: props.busy,
    onSelect: () => (showVoidInput.value = true),
  })
  return list
})
</script>

<template>
  <section class="review-head" :aria-label="t('work.room.review.label')">
    <div class="review-head__title-row">
      <h2 class="t-title review-head__title">{{ title }}</h2>
      <div class="review-head__tools">
        <BaseButton
          v-if="card.pr_url"
          kind="ghost"
          size="sm"
          append-icon="mdi-open-in-new"
          :href="card.pr_url"
          target="_blank"
          rel="noopener"
        >
          PR #{{ card.pr_number }}
        </BaseButton>
        <BaseButton
          v-if="card.deliverable?.kind === 'file' && card.deliverable.filename"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-tray-arrow-down"
          :loading="deliverableBusy"
          @click="emit('download')"
        >
          {{ t('work.room.accept.download') }}
        </BaseButton>
        <BaseButton
          v-else-if="card.deliverable?.kind === 'link' && card.deliverable.url"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-open-in-new"
          :href="card.deliverable.url"
          target="_blank"
          rel="noopener"
        >
          {{ t('work.room.review.openLink') }}
        </BaseButton>
        <AdaptiveMenu :actions="moreActions">
          <template #activator="{ props: menuProps }">
            <BaseButton
              v-bind="menuProps"
              kind="ghost"
              size="sm"
              icon="mdi-dots-horizontal"
              :title="t('work.room.review.more')"
              :aria-label="t('work.room.review.more')"
            />
          </template>
        </AdaptiveMenu>
      </div>
    </div>
    <!-- 交出去的那一份：点采纳之前看得见是不是这一份文件、这一个地址。 -->
    <p v-if="card.deliverable?.kind === 'file' && card.deliverable.filename" class="review-head__line">
      {{ card.deliverable.filename }}
    </p>
    <p v-else-if="card.deliverable?.kind === 'link' && card.deliverable.url" class="review-head__line">
      {{ card.deliverable.url }}
    </p>
    <div v-if="deliverableError" role="alert" class="review-head__error">{{ deliverableError }}</div>

    <!-- 冲突卡：芝士在处理，处理完可以重新采纳。 -->
    <p v-if="card.status === 'conflict'" class="review-head__line">
      {{ card.note || t('work.room.accept.conflictFallback') }}
      <i18n-t scope="global" keypath="work.room.accept.conflictWorking" tag="span">
        <template #agent><UserRef :handle="agentHandle" :name="agentName" /></template>
      </i18n-t>
    </p>
    <!-- 后端写在卡上的 note（比如「PR 有新提交，之前看到的版本已过时」）。 -->
    <AcceptNoteLine v-if="note" :text="note.text" :tone="note.tone" />
    <i18n-t
      v-if="autoMergeArmedBy"
      scope="global"
      keypath="work.room.review.autoMergeOn"
      tag="p"
      class="review-head__line"
    >
      <template #who><UserRef :handle="autoMergeArmedBy" /></template>
    </i18n-t>

    <div v-if="signals.length" class="review-head__signals">
      <button
        v-for="s in signals"
        :key="s.key"
        type="button"
        class="signal"
        :class="{ 'signal--open': openSignal === s.key, 'signal--static': !s.more }"
        :aria-expanded="s.more ? openSignal === s.key : undefined"
        @click="toggleSignal(s)"
      >
        <v-icon v-if="s.icon" size="16" :style="{ color: s.iconColor }">{{ s.icon }}</v-icon>
        <span v-else class="signal__dot" :style="s.dot" aria-hidden="true" />
        <span>{{ s.label }}</span>
        <v-icon v-if="s.more" size="14" class="signal__caret">{{
          openSignal === s.key ? 'mdi-chevron-up' : 'mdi-chevron-down'
        }}</v-icon>
      </button>
    </div>
    <!-- 展开的那一个信号的明细。 -->
    <div v-if="openSignal === 'checks'" class="signal-detail">
      <div v-for="chk in checkRows" :key="chk.name" class="signal-detail__row">
        <v-icon
          size="14"
          :style="{
            color: FAILED.includes(chk.conclusion ?? '')
              ? 'var(--danger)'
              : chk.status !== 'completed'
                ? 'var(--faint)'
                : 'var(--ok)',
          }"
        >
          {{
            FAILED.includes(chk.conclusion ?? '')
              ? 'mdi-close-circle'
              : chk.status !== 'completed'
                ? 'mdi-progress-clock'
                : 'mdi-check-circle'
          }}
        </v-icon>
        <a v-if="chk.url" :href="chk.url" target="_blank" rel="noopener" class="signal-detail__name">{{ chk.name }}</a>
        <span v-else class="signal-detail__name">{{ chk.name }}</span>
        <span v-if="chk.status !== 'completed'" class="c-faint">{{ t('work.room.accept.checkRunning') }}</span>
      </div>
    </div>
    <div v-else-if="openSignal === 'merge'" class="signal-detail">
      <div v-for="(r, i) in reasons" :key="i" class="signal-detail__row">
        <span>{{ r.detail }}</span>
        <code v-for="chk in r.checks" :key="chk">{{ chk }}</code>
      </div>
      <!-- 托管方自己的一句话（I23）：这次采纳会落到哪里、有没有外部检查。 -->
      <div v-if="forgeDeclaration" class="signal-detail__row c-muted">{{ forgeDeclaration }}</div>
    </div>
    <div v-else-if="openSignal === 'approvals'" class="signal-detail">
      <div class="signal-detail__row">
        <span v-if="card.approvals.length">
          <template v-for="(h, i) in card.approvals" :key="h"
            >{{ i ? t('work.room.roster.listSeparator') : '' }}<UserRef :handle="h"
          /></template>
        </span>
        <span v-else class="c-muted">{{ t('work.room.review.noApprovals') }}</span>
        <BaseButton
          v-if="!card.approvals.includes(myHandle)"
          kind="secondary"
          size="sm"
          :disabled="busy"
          prepend-icon="mdi-thumb-up-outline"
          @click="emit('approve')"
        >
          {{ t('work.room.accept.approve') }}
        </BaseButton>
        <span v-else class="c-muted">{{ t('work.room.accept.youApproved') }}</span>
      </div>
    </div>

    <!-- 审阅重点：交付的人请你确认的那几件事，一行一条（后端限三条）。 -->
    <div v-if="focusItems.length" class="review-head__focus">
      <div class="t-eyebrow">{{ t('work.room.accept.focus') }}</div>
      <ol>
        <li v-for="(item, i) in focusItems" :key="i">{{ item }}</li>
      </ol>
    </div>

    <!-- 机器闸门 (eval C2, 已退役) 的历史读数：退役之前递的卡才有，绝不画成绿勾——当年
         跑的是项目自己配的检查命令，不是完整 CI。 -->
    <p v-if="card.gate_passed_at" class="review-head__line">{{ t('work.room.accept.gatePassed') }}</p>

    <AdaptiveDialog v-model="reassignOpen" :title="t('work.room.accept.reassignTitle')" size="sm">
      <v-list density="compact" class="py-0">
        <v-list-item
          v-for="mbr in reviewerChoices"
          :key="mbr.user_handle"
          :active="mbr.user_handle === card.reviewer_handle"
          @click="reassign(mbr.user_handle)"
        >
          <v-list-item-title class="text-body-2">
            {{ memberName(mbr) || mbr.user_handle }}
            <span v-if="memberName(mbr)" class="c-muted">{{ mbr.user_handle }}</span>
          </v-list-item-title>
          <v-list-item-subtitle class="text-caption">{{ mbr.role }}</v-list-item-subtitle>
        </v-list-item>
        <v-list-item v-if="reviewerChoices.length === 0">
          <v-list-item-title class="text-caption c-muted">{{ t('work.room.accept.noMembers') }}</v-list-item-title>
        </v-list-item>
      </v-list>
    </AdaptiveDialog>

    <!-- 作废：卡停在一个没人能推进的地方时的出口。不合并，也不退回修改。 -->
    <ConfirmDialog
      v-model="showVoidInput"
      :title="t('work.room.review.voidTitle')"
      :confirm-label="t('work.room.accept.void')"
      danger
      :loading="busy"
      @confirm="emit('void')"
    >
      <p class="mb-3">{{ t('work.room.accept.voidHint') }}</p>
      <v-text-field
        v-model="voidNote"
        autocomplete="off"
        variant="outlined"
        density="compact"
        hide-details
        :label="t('work.room.accept.voidReason')"
      />
    </ConfirmDialog>

    <!-- 人工放行 (#718)：明知合并态不是 clean 仍合并。红着合有时候是对的，不能接受的是
         没有人做过这个决定，所以要填理由，点下去在卡上留名。 -->
    <ConfirmDialog
      v-model="showForceMergeInput"
      :title="t('work.room.review.forceMergeTitle')"
      :confirm-label="t('work.room.accept.forceMerge')"
      danger
      :loading="busy"
      @confirm="emit('force-merge')"
    >
      <p class="mb-3">{{ t('work.room.accept.forceMergeWarning') }}</p>
      <v-textarea
        v-model="forceMergeReason"
        autocomplete="off"
        variant="outlined"
        density="compact"
        rows="2"
        auto-grow
        hide-details
        :label="t('work.room.accept.forceMergeReason')"
      />
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.review-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
}
.review-head__title-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.review-head__title {
  flex: 1 1 auto;
  min-width: 0;
  margin: 2px 0 0;
}
.review-head__tools {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
}
.review-head__line {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.review-head__error {
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.review-head__signals {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.signal {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.signal:hover:not(.signal--static),
.signal--open {
  background: var(--fill);
}
.signal--static {
  cursor: default;
}
.signal__dot {
  flex: none;
  width: 10px;
  height: 10px;
  border: 2px solid var(--faint);
  border-radius: 50%;
}
.signal__caret {
  color: var(--faint);
}
.signal-detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.signal-detail__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.signal-detail__name {
  color: var(--text);
}
.review-head__focus {
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.review-head__focus ol {
  margin: 4px 0 0;
  padding-left: 20px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}
.review-head__focus li + li {
  margin-top: 4px;
}
</style>
