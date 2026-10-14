<script setup lang="ts">
// 市场（容器）：读模型与工作电脑的目录、节点状态（每 15 秒刷一次，见
// composables/useMarketNodes），画面全在同目录的 MarketViewView。页签状态放在这里：
// 节点状态板在「模型与工作电脑」页签上，用户第一次点开它才开始读节点、起表；技能市场的
// 目录与「我有没有机器」同理，第一次点开「技能与插件」页签才读。
import type { SkillMarket as SkillMarketData } from '@/api/machineSkills'
import type { MarketPools } from '@/cx_types'

import { onMounted, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { useMarketNodes } from '@/composables/useMarketNodes'

import { getMarketPools, listMyDevices } from '@/api'
import { fetchMachineSkill, fetchSkillMarket } from '@/api/machineSkills'
import { t } from '@/i18n'
import { pickMyDevice } from '@/lib/myDevice'
import MarketViewView from '@/views/MarketViewView.vue'

const pools = ref<MarketPools | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const tab = ref<'tasks' | 'pools' | 'skills'>('tasks')
const nodes = useMarketNodes({ enabled: () => tab.value === 'pools' })

// 技能市场：公开目录，和哪台机器无关；但「装」要有个落点，所以连「我的一台机器」一起
// 读（复用 lib/myDevice 那套猜法，和队友技能面板同一份）。读完了才知道有没有机器。
const market = ref<SkillMarketData | null>(null)
const marketLoading = ref(false)
const marketLoaded = ref(false)
const deviceId = ref<string | null>(null)
const scanning = ref(false)

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

// 第一次点开「技能与插件」页签才读目录：没人看的时候多读一次，只是一次没人看的网络调用。
let marketStarted = false
watch(tab, () => {
  if (tab.value !== 'skills' || marketStarted) return
  marketStarted = true
  void loadMarket()
})

async function loadMarket() {
  marketLoading.value = true
  // 目录与「我有没有机器」互不依赖，一起发；各自失败只丢自己那份。
  const [m] = await Promise.all([
    fetchSkillMarket().then(
      (r) => r,
      () => null
    ),
    listMyDevices().then(
      (r) => {
        deviceId.value = pickMyDevice(r.devices)?.device_id ?? null
      },
      () => {
        deviceId.value = null
      }
    ),
  ])
  market.value = m
  marketLoading.value = false
  marketLoaded.value = true
}

/** 从网上取一份：检验后直接安装到当前工作电脑。 */
async function install({ url, kind }: { url: string; kind: 'skill' | 'plugin' }) {
  const device = deviceId.value
  if (!device || scanning.value) return
  scanning.value = true
  try {
    await fetchMachineSkill(device, url, null, kind)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('work.room.skills.fetchFailed'))
  } finally {
    scanning.value = false
  }
}
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
    :market="market"
    :market-loading="marketLoading"
    :market-loaded="marketLoaded"
    :device-id="deviceId"
    :scanning="scanning"
    @retry="load"
    @install="install"
  />
</template>
