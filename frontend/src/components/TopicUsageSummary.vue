<script setup lang="ts">
// 频道详情「关于」里的用量：本频道、全项目各一行，运行次数、token、输入、输出、费用各占
// 一格。费用说不准（订阅、没有价目）时的那句说明在费用那一格的悬停里。
import type { UsageStats } from '@/cx_types'

import { t } from '@/i18n'
import { costLabel, costNote, fmtNum } from '@/lib/usageFormat'

defineProps<{
  loading: boolean
  topicUsage: UsageStats | null
  projectUsage: UsageStats | null
}>()

const COLUMNS = ['turns', 'tokens', 'input', 'output', 'cost'] as const
</script>

<template>
  <table class="usage-table" data-testid="channel-usage">
    <thead>
      <tr>
        <td />
        <th v-for="column in COLUMNS" :key="column" scope="col">{{ t(`work.channelDetails.usage.${column}`) }}</th>
      </tr>
    </thead>
    <tbody>
      <!-- Both rows are always there; until the numbers arrive each holds a placeholder bar of the
           same height, so the table opens at its final size. -->
      <tr
        v-for="row in [
          { key: 'topic', label: t('work.room.menu.thisTopic'), u: topicUsage },
          { key: 'project', label: t('work.room.menu.wholeProject'), u: projectUsage },
        ]"
        :key="row.key"
      >
        <th scope="row">{{ row.label }}</th>
        <template v-if="row.u">
          <td>{{ fmtNum(row.u.turns) }}</td>
          <td>{{ fmtNum(row.u.total_tokens) }}</td>
          <td>{{ fmtNum(row.u.input_tokens) }}</td>
          <td>{{ fmtNum(row.u.output_tokens) }}</td>
          <td :title="costNote(row.u) || undefined">{{ costLabel(row.u) }}</td>
        </template>
        <td v-else-if="loading" :colspan="COLUMNS.length">
          <span class="usage-table__placeholder" aria-hidden="true" />
        </td>
        <td v-else :colspan="COLUMNS.length" class="usage-table__none">{{ t('work.room.menu.noUsage') }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.usage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: var(--lh-13);
}
.usage-table th,
.usage-table td {
  padding: 2px 0 2px 12px;
  text-align: end;
  white-space: nowrap;
}
.usage-table thead th {
  color: var(--muted);
  font-weight: 400;
}
.usage-table tbody th {
  padding-inline-start: 0;
  color: var(--text);
  font-weight: 400;
  text-align: start;
}
.usage-table tbody td {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.usage-table__none {
  text-align: start;
}
/* Same line box as a row of numbers: when they arrive the row does not jump. */
.usage-table__placeholder {
  display: inline-block;
  width: 12em;
  height: 10px;
  vertical-align: middle;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
}
</style>
