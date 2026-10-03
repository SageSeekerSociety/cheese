<script setup lang="ts">
import type { ProjectRow, ProjectsPayload } from '@/lib/adminModels'

import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminGrid from '@/components/admin/AdminGrid.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { fmtCost, fmtNum } from '@/lib/usageFormat'

// 额度那一段：给项目的网关 key 设「刹车值」。和模型分开，因为它们回答的是两个不同的
// 问题（「供给什么」和「谁在用、封顶多少」），挤在一张表里会让两件事互相稀释。
//
// 一眼要看出四件事：剩余 / 已用 / 刹车值 / 是不是不限量。不限量是一个**结论**，所以它
// 先说（让读者去比 total 和 used 是把结论留给读者算）。「设额度」只往上发一行 ——
// 框和接口在页面那一侧。
const props = defineProps<{
  /** `GET /model/projects` 的响应；`null` = 还没到货（或读失败，见 `state`）。 */
  projects: ProjectsPayload | null
  /** 这一段在加载中。骨架只在手上一条都没有时画。 */
  loading: boolean
  /** 表体画真行还是那条读失败的说明。这一段自己拉、也自己失败，和模型段无关。 */
  state: 'rows' | 'error'
  /** 读失败的原话。**不能**退化成一张空表 —— 空表说的是「还没有项目」。 */
  error: string | null
}>()

const emit = defineEmits<{ retry: []; 'set-budget': [row: ProjectRow] }>()

const { t } = useI18n()
</script>

<template>
  <div class="amd__gridwrap amd__gridwrap--short">
    <AdminGrid
      :label="t('models.budget.label')"
      :cols="['240px', '132px', '170px', '186px', '150px', '96px']"
      :bone-widths="['60%', '52%', '64%', '58%', '56%', '48%']"
      :loading="props.loading && !props.projects"
      :skeleton-rows="5"
      :state="props.state"
      cards
      :empty="!props.error && props.projects && !props.projects.projects.length ? t('models.budget.empty') : null"
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('models.budget.column.project') }}</th>
          <th scope="col">{{ t('models.budget.column.spend') }}</th>
          <th scope="col">{{ t('models.budget.column.usage') }}</th>
          <th scope="col">{{ t('models.budget.column.credits') }}</th>
          <th scope="col">{{ t('models.budget.column.brake') }}</th>
          <th scope="col" class="amd__num">{{ t('models.table.column.actions') }}</th>
        </tr>
      </template>

      <!-- 读失败给中性标题、原话落到说明行、并给重试；**不**退化成空表（空表说的是
       「还没有项目」）。它和模型段各说自己的那一次失败，不由一个页面级的横幅代劳。 -->
      <template #error>
        <AdminEmptyState
          compact
          tone="error"
          :title="t('models.budget.loadFailed')"
          :desc="props.error || undefined"
          :action="t('models.page.retry')"
          @action="emit('retry')"
        />
      </template>

      <template #empty>
        <AdminEmptyState compact :title="t('models.budget.empty')" />
      </template>

      <tr v-for="row in props.projects?.projects ?? []" :key="row.project_id" class="amd__row">
        <td class="amd__cell" data-card="primary">
          <span class="amd__nameStatic" :title="row.name">{{ row.name }}</span>
        </td>
        <td class="amd__cell t-num" :data-label="t('models.budget.column.spend')">
          {{ fmtCost(row.gateway_spend_usd) }}
        </td>
        <td class="amd__cell" :data-label="t('models.budget.column.usage')">
          <span class="amd__usage">
            <span class="t-num amd__usageMain">{{ fmtNum(row.usage.requests) }}</span>
            <span class="t-meta-read amd__dim"
              >{{ fmtNum(row.usage.total_tokens) }} {{ t('models.usage.tokens') }}</span
            >
          </span>
        </td>
        <td class="amd__cell" :data-label="t('models.budget.column.credits')">
          <!-- 不限量是一个**结论**，不是一个大数字，所以它先说，别让读者去比 total 和 used。 -->
          <span v-if="row.credits.unlimited" class="amd__tag">{{ t('models.budget.unlimited') }}</span>
          <span v-else class="amd__usage">
            <span class="t-num amd__usageMain">{{ fmtNum(row.credits.remaining) }}</span>
            <span class="t-meta-read amd__dim">
              {{
                t('models.budget.used', {
                  used: fmtNum(row.credits.used),
                  total: fmtNum(row.credits.total ?? 0),
                })
              }}
            </span>
          </span>
        </td>
        <td class="amd__cell" :data-label="t('models.budget.column.brake')">
          <span v-if="row.max_budget_usd !== null" class="amd__usage">
            <span class="t-num amd__usageMain">{{ fmtCost(row.max_budget_usd) }}</span>
            <span v-if="row.budget_override_usd !== null" class="t-meta-read amd__dim">
              {{ t('models.budget.override') }}
            </span>
          </span>
          <span v-else class="amd__usage">
            <span class="amd__dim">{{ t('models.budget.none') }}</span>
            <span v-if="row.budget_derived_usd !== null" class="t-meta-read amd__dim">
              {{ t('models.budget.derived', { value: fmtCost(row.budget_derived_usd) }) }}
            </span>
          </span>
        </td>
        <td class="amd__cell amd__cell--actions" :data-label="t('models.table.column.actions')">
          <BaseButton kind="secondary" size="sm" :disabled="!row.has_key" @click="emit('set-budget', row)">
            {{ t('models.budget.action.set') }}
          </BaseButton>
        </td>
      </tr>
    </AdminGrid>
  </div>
</template>

<style scoped>
/* 和模型表同一条规则（高度上限让表头在里面 sticky），只有一档更矮：这一段是配角，
   五行上下，占满 56vh 会把下面那段挤到屏幕外。 */
.amd__gridwrap {
  display: flex;
  max-height: 56vh;
  min-height: 220px;
}

.amd__gridwrap--short {
  max-height: 40vh;
}

.amd__row {
  height: 44px;
}

.amd__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__num {
  text-align: right;
}

.amd__cell--actions {
  text-align: right;
  white-space: nowrap;
}

.amd__nameStatic {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
}

.amd__dim {
  color: var(--muted);
}

.amd__tag {
  display: inline-block;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.amd__usage {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.amd__usageMain {
  color: var(--ink);
}
</style>
