<script setup lang="ts">
import type { CreditTeamPage, CreditTeamRow, Plan } from '@/lib/adminCredits'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import { availableCredits, fmtCredits, meterTone, periodUse, teamTitle } from '@/lib/adminCredits'

// 团队一览：挂在哪个方案上、本月方案额度用了多少、手上还能花多少。点一行打开这个团队。
const props = defineProps<{
  /** 当前这一页；`null` = 还没到货（或读失败）。 */
  page: CreditTeamPage | null
  /** 方案表，用来把 `plan_key` 翻成名字、判断是否不限。 */
  plans: Plan[]
  loading: boolean
  error: string | null
  /** 正在搜索：空态说「没有匹配的」而不是「还没有团队」。 */
  searching: boolean
}>()

const emit = defineEmits<{ open: [row: CreditTeamRow]; page: [page: number]; retry: [] }>()

const { t, locale } = useI18n()

const planByKey = computed(() => new Map(props.plans.map((plan) => [plan.key, plan])))
const pageCount = computed(() => (props.page ? Math.max(1, Math.ceil(props.page.total / props.page.page_size)) : 1))

function teamMeta(row: CreditTeamRow): string {
  if (row.personal_owner) {
    return `${t('credits.teams.kindPersonal')} · @${row.personal_owner}`
  }
  return row.member_count === null
    ? t('credits.teams.kindTeam')
    : `${t('credits.teams.kindTeam')} · ${t('credits.teams.members', { n: row.member_count })}`
}

/** 本月方案额度那一格：不限画空心槽，按时间窗口限额或还没发只写一句，发了画已用的比例。 */
function meter(row: CreditTeamRow) {
  const use = periodUse(row.period, planByKey.value.get(row.plan_key) ?? null)
  if (use.kind === 'unlimited') {
    return { kind: use.kind, label: t('credits.unlimited'), value: '', limit: null, ratio: 0, tone: 'ink' as const }
  }
  if (use.kind === 'windows') {
    return {
      kind: use.kind,
      label: t('credits.teams.windowed'),
      value: '',
      limit: null,
      ratio: 0,
      tone: 'ink' as const,
    }
  }
  if (use.kind === 'notIssued') {
    return {
      kind: use.kind,
      label: t('credits.teams.notIssued'),
      value: '',
      limit: null,
      ratio: 0,
      tone: 'ink' as const,
    }
  }
  let label = t('credits.teams.used', { pct: Math.round(use.ratio * 100) })
  if (use.used <= 0) label = t('credits.teams.unused')
  else if (use.ratio >= 1) label = t('credits.teams.usedUp')
  return {
    kind: use.kind,
    label,
    value: `${fmtCredits(use.used, locale.value)} / ${fmtCredits(use.total, locale.value)}`,
    limit: use.total,
    ratio: use.ratio,
    tone: meterTone(use.ratio),
  }
}

function balanceText(row: CreditTeamRow): string {
  const left = availableCredits(row.packs, row.period, planByKey.value.get(row.plan_key) ?? null)
  return left === null ? t('credits.unlimited') : fmtCredits(left, locale.value)
}
</script>

<template>
  <BaseTable
    class="act"
    :label="t('credits.teams.label')"
    :cols="[null, '140px', '280px', '120px']"
    :loading="props.loading && !props.page"
    :busy="props.loading && !!props.page"
    :skeleton-rows="6"
    :state="props.error !== null ? 'error' : 'rows'"
    cards
    :empty="
      props.page && !props.page.items.length
        ? props.searching
          ? t('credits.teams.noMatch')
          : t('credits.teams.empty')
        : null
    "
  >
    <template #head>
      <tr>
        <th scope="col">{{ t('credits.teams.column.team') }}</th>
        <th scope="col">{{ t('credits.teams.column.plan') }}</th>
        <th scope="col">{{ t('credits.teams.column.period') }}</th>
        <th scope="col" class="act__num">{{ t('credits.teams.column.balance') }}</th>
      </tr>
    </template>

    <template #error>
      <BaseLoadError
        :title="t('credits.teams.loadFailed')"
        :error="props.error || undefined"
        :retry-label="t('credits.teams.retry')"
        @retry="emit('retry')"
      />
    </template>

    <tr v-for="row in props.page?.items ?? []" :key="row.id" class="act__row" @click="emit('open', row)">
      <td data-card="primary">
        <!-- 名字是打开这个团队的入口：键盘和读屏走它，整行点击是给指针的便利。 -->
        <button type="button" class="act__team" @click.stop="emit('open', row)">
          <span class="act__name" data-user-content>{{ teamTitle(row) }}</span>
          <span class="act__meta t-meta-read">{{ teamMeta(row) }}</span>
        </button>
      </td>
      <td :data-label="t('credits.teams.column.plan')">
        {{ planByKey.get(row.plan_key)?.name ?? row.plan_key }}
      </td>
      <td :data-label="t('credits.teams.column.period')">
        <span
          v-if="meter(row).kind === 'notIssued' || meter(row).kind === 'windows'"
          class="act__notissued t-meta-read"
          >{{ meter(row).label }}</span
        >
        <AdminMeterBar
          v-else
          :label="meter(row).label"
          :value-text="meter(row).value"
          :limit="meter(row).limit"
          :ratio="meter(row).ratio"
          :tone="meter(row).tone"
        />
      </td>
      <td class="act__num t-num" :data-label="t('credits.teams.column.balance')">{{ balanceText(row) }}</td>
    </tr>

    <template v-if="pageCount > 1" #foot>
      <v-pagination
        :model-value="props.page?.page ?? 1"
        :length="pageCount"
        density="compact"
        :total-visible="7"
        @update:model-value="emit('page', $event)"
      />
    </template>
  </BaseTable>
</template>

<style scoped>
/* 四列放得下：不要表壳那条为七八列宽表准备的横滚下限。窄屏走卡片模式。 */
.act :deep(.agrid__table) {
  min-width: 640px;
}

.act__notissued {
  color: var(--muted);
}

.act__row {
  cursor: pointer;
}

.act__team {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  max-width: 100%;
  padding: 0;
  background: none;
  border: 0;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.act__team:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.act__name {
  overflow: hidden;
  max-width: 100%;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.act__meta {
  color: var(--muted);
}

.act__num {
  text-align: right;
}
</style>
