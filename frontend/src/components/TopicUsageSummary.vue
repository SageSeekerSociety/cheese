<script setup lang="ts">
// 话题 ⋯ 里的用量：本话题、全项目各一行（次数 · token · 费用），编号垫在最底下。
// 桌面上它在 ⋯ 弹出的小卡片里，手机上在底部面板里——同一块内容，两种摆法。
import type { UsageStats } from '@/cx_types'

import { t } from '@/i18n'
import { costLabel, costNote, fmtNum } from '@/lib/usageFormat'

defineProps<{
  loading: boolean
  topicUsage: UsageStats | null
  projectUsage: UsageStats | null
  /** 话题的短编号；项目本体没有。 */
  shortId?: string | null
  /** 完整 id，悬停时看。 */
  topicId?: string
}>()

// 一行一个范围：次数 · token · 费用。输入 / 输出的拆分和费用的说明放在 title 里，
// 原来那十个大格子里有八个在一个新话题上都是 0。
function usageLine(u: UsageStats): string {
  return t('work.room.menu.usageLine', { turns: fmtNum(u.turns), tokens: fmtNum(u.total_tokens), cost: costLabel(u) })
}
function usageTitle(u: UsageStats): string {
  const split = t('work.room.menu.usageSplit', { input: fmtNum(u.input_tokens), output: fmtNum(u.output_tokens) })
  const note = costNote(u)
  return note ? `${split}\n${note}` : split
}
</script>

<template>
  <div class="usage-summary">
    <div class="usage-summary__usage">
      <div class="usage-summary__label">{{ t('work.room.menu.usage') }}</div>
      <div v-if="loading" class="d-flex justify-center py-2">
        <v-progress-circular indeterminate color="primary" size="20" />
      </div>
      <template v-else>
        <div
          v-for="row in [
            { label: t('work.room.menu.thisTopic'), u: topicUsage },
            { label: t('work.room.menu.wholeProject'), u: projectUsage },
          ]"
          :key="row.label"
          class="usage-row"
          :title="row.u ? usageTitle(row.u) : undefined"
        >
          <span>{{ row.label }}</span>
          <span v-if="row.u" class="usage-row__value">{{ usageLine(row.u) }}</span>
          <span v-else class="usage-row__value">{{ t('work.room.menu.noUsage') }}</span>
        </div>
      </template>
    </div>
    <div v-if="shortId" class="usage-summary__foot t-meta" :title="topicId">#{{ shortId }}</div>
  </div>
</template>

<style scoped>
.usage-summary__usage {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 16px;
}
.usage-summary__label {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.usage-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.usage-row__value {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.usage-summary__foot {
  padding: 8px 16px 4px;
  border-top: 1px solid var(--line);
  color: var(--faint);
}
</style>
