<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.detail.manageComputePool.title')" color="transparent" density="compact">
      <template #append>
        <v-btn icon="mdi-refresh" variant="text" :loading="loading" @click="load"></v-btn>
      </template>
    </v-toolbar>

    <div v-if="loading && !pool" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else-if="pool">
      <v-card flat rounded="lg" class="mx-4 mb-4 pa-4">
        <p class="text-body-2 text-medium-emphasis mb-3">
          {{ t('spaces.detail.manageComputePool.intro') }}
        </p>

        <template v-if="pool.has_pool">
          <div class="d-flex align-center mb-2">
            <span class="text-h6 me-2">{{ formatCredits(pool.credits_remaining) }}</span>
            <span class="text-body-2 text-medium-emphasis">
              {{ t('spaces.detail.manageComputePool.remainingHint') }}
            </span>
            <v-spacer></v-spacer>
            <v-chip v-if="pool.exhausted" color="error" size="small" variant="tonal">
              {{ t('spaces.detail.manageComputePool.exhausted') }}
            </v-chip>
            <v-chip v-else-if="pool.needs_attention" color="warning" size="small" variant="tonal">
              {{ t('spaces.detail.manageComputePool.nearlyExhausted') }}
            </v-chip>
            <v-chip v-else color="success" size="small" variant="tonal">
              {{ t('spaces.detail.manageComputePool.healthy') }}
            </v-chip>
          </div>

          <v-progress-linear
            :model-value="usagePercent"
            :color="pool.exhausted ? 'error' : pool.needs_attention ? 'warning' : 'primary'"
            height="10"
            rounded
          ></v-progress-linear>

          <div class="d-flex text-body-2 text-medium-emphasis mt-2">
            <span>{{ t('spaces.detail.manageComputePool.used', { used: formatCredits(pool.credits_used) }) }}</span>
            <v-spacer></v-spacer>
            <span>{{ t('spaces.detail.manageComputePool.total', { total: formatCredits(pool.credits_total) }) }}</span>
          </div>

          <p class="text-body-2 text-medium-emphasis mt-3 mb-0">
            {{ t('spaces.detail.manageComputePool.alertLine', { ratio: alertPercent }) }}
          </p>
          <!--
            共享池会超支，这是设计如此：闸门只在进场时看一次，扣费在结算时落账。
            所以这里不能跟老师承诺「到 0 就停」，只能说明超支的量级。
          -->
          <p class="text-body-2 text-medium-emphasis mb-0">
            {{ t('spaces.detail.manageComputePool.overspendNote') }}
          </p>
        </template>

        <template v-else>
          <p class="text-body-1 mb-1">{{ t('spaces.detail.manageComputePool.noPool') }}</p>
          <!--
            没买池子 = 不限额。这一点是整个功能的边界：额度池只影响真的买了池子的
            空间，别的空间和项目一个都不受影响。
          -->
          <p class="text-body-2 text-medium-emphasis mb-0">
            {{ t('spaces.detail.manageComputePool.noPoolHint') }}
          </p>
        </template>

        <v-divider class="my-4"></v-divider>

        <div class="text-subtitle-2 font-weight-medium mb-2">
          {{ t('spaces.detail.manageComputePool.fundTitle') }}
        </div>
        <div class="d-flex align-start ga-2">
          <v-text-field
            v-model.number="amount"
            type="number"
            min="0"
            step="1"
            hide-details
            density="comfortable"
            variant="outlined"
            :label="t('spaces.detail.manageComputePool.amount')"
            :error-messages="amountError ? [t('spaces.detail.manageComputePool.invalidAmount')] : []"
            style="max-width: 240px"
          ></v-text-field>
          <v-btn color="primary" :loading="funding" :disabled="!canFund" @click="fund">
            {{ t('spaces.detail.manageComputePool.fund') }}
          </v-btn>
        </div>
        <p class="text-body-2 text-medium-emphasis mt-2 mb-0">
          {{ t('spaces.detail.manageComputePool.topUpNote') }}
        </p>
      </v-card>

      <v-toolbar
        :title="t('spaces.detail.manageComputePool.perProject')"
        color="transparent"
        density="compact"
      ></v-toolbar>

      <v-table v-if="pool.projects.length > 0" density="comfortable">
        <thead>
          <tr>
            <th>{{ t('spaces.detail.manageComputePool.project') }}</th>
            <th class="text-end">{{ t('spaces.detail.manageComputePool.cost') }}</th>
            <th class="text-end">{{ t('spaces.detail.manageComputePool.tokens') }}</th>
            <th class="text-end">{{ t('spaces.detail.manageComputePool.turns') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="project in pool.projects" :key="project.project_id">
            <td>{{ project.name || project.project_id }}</td>
            <td class="text-end">{{ formatCredits(project.cost_usd) }}</td>
            <td class="text-end">{{ project.total_tokens }}</td>
            <td class="text-end">{{ project.turns }}</td>
          </tr>
        </tbody>
      </v-table>

      <v-sheet v-else class="pa-4 text-center">
        <p class="text-medium-emphasis mb-0">{{ t('spaces.detail.manageComputePool.noSpend') }}</p>
      </v-sheet>
    </template>

    <v-sheet v-else class="pa-4 text-center">
      <p class="text-medium-emphasis mb-0">{{ t('spaces.detail.manageComputePool.loadFailed') }}</p>
    </v-sheet>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceComputePool } from '@/network/api/spaces/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()

const pool = ref<SpaceComputePool | null>(null)
const loading = ref(false)
const funding = ref(false)
const amount = ref<number | null>(null)

const spaceId = computed(() => {
  const id = Number(spaceStore.currentSpaceId)
  return Number.isFinite(id) ? id : 0
})

const usagePercent = computed(() => Math.min(100, Math.round((pool.value?.credits_ratio ?? 0) * 100)))
const alertPercent = computed(() => Math.round((pool.value?.alert_ratio ?? 0.8) * 100))

// 后端只认正数（HTTP 400），所以按钮的禁用状态要和那条规则对得上。
// 前端拦住不是为了让后端轻松，而是让老师在点下去之前就看见问题。
const amountError = computed(() => amount.value !== null && (!Number.isFinite(amount.value) || amount.value <= 0))
const canFund = computed(() => amount.value !== null && !amountError.value)

const formatCredits = (value: number) => value.toFixed(2)

const load = async () => {
  if (!spaceId.value) return
  loading.value = true
  try {
    const { data } = await SpacesApi.getComputePool(spaceId.value)
    pool.value = data
  } catch (error) {
    console.error('获取额度池失败:', error)
    pool.value = null
  } finally {
    loading.value = false
  }
}

const fund = async () => {
  if (!canFund.value || amount.value === null) return
  funding.value = true
  try {
    // 返回的就是充值后的池子，直接拿来刷新，省一次往返也少一个中间态。
    const { data } = await SpacesApi.fundComputePool(spaceId.value, { credits: amount.value })
    pool.value = data
    amount.value = null
    toast.success(t('spaces.detail.manageComputePool.fundSuccess'))
  } catch (error) {
    console.error('充值额度池失败:', error)
    toast.error(t('spaces.detail.manageComputePool.fundFailed'))
  } finally {
    funding.value = false
  }
}

onMounted(load)
</script>
