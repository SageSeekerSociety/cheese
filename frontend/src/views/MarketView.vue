<script setup lang="ts">
// 市场（容器）：读模型与工作电脑的目录、节点状态（每 15 秒刷一次，见
// composables/useMarketNodes），画面全在同目录的 MarketViewView。页签状态放在这里：
// 节点状态板在「模型与工作电脑」页签上，用户第一次点开它才开始读节点、起表。
import type { MarketPools } from '@/cx_types'

import { onMounted, ref } from 'vue'

import { useMarketNodes } from '@/composables/useMarketNodes'

import { getMarketPools } from '@/api'
import { t } from '@/i18n'
import MarketViewView from '@/views/MarketViewView.vue'

const pools = ref<MarketPools | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const tab = ref<'tasks' | 'pools'>('tasks')
const nodes = useMarketNodes({ enabled: () => tab.value === 'pools' })

async function load() {
  loading.value = true
  error.value = null
  try {
    pools.value = await getMarketPools()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('market.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <MarketViewView
    v-model:tab="tab"
    :pools="pools"
    :loading="loading"
    :error="error"
    :nodes="nodes.board.value"
    :nodes-loading="nodes.loading.value"
    :nodes-error="nodes.error.value"
    @retry="load"
  />
</template>
