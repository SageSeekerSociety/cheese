<script setup lang="ts">
// 市场（容器）：开页就读技能市场的目录和「我的一台机器」，画面全在同目录的 MarketViewView。
import type { SkillMarket as SkillMarketData } from '@/api/machineSkills'

import { onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { listMyDevices } from '@/api'
import { fetchMachineSkill, fetchSkillMarket } from '@/api/machineSkills'
import { t } from '@/i18n'
import { pickMyDevice } from '@/lib/myDevice'
import MarketViewView from '@/views/MarketViewView.vue'

// 技能市场：公开目录，和哪台机器无关；但「装」要有个落点，所以连「我的一台机器」一起
// 读（复用 lib/myDevice 那套猜法，和队友技能面板同一份）。读完了才知道有没有机器。
const market = ref<SkillMarketData | null>(null)
const marketLoading = ref(false)
const marketLoaded = ref(false)
const deviceId = ref<string | null>(null)
const scanning = ref(false)

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

onMounted(loadMarket)

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
    :market="market"
    :market-loading="marketLoading"
    :market-loaded="marketLoaded"
    :device-id="deviceId"
    :scanning="scanning"
    @install="install"
  />
</template>
