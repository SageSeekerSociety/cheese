<script setup lang="ts">
/**
 * 后台「机器」（`/admin/machines`）：MicroCloud 上的宿主机、每台上的沙箱、温机池，和过去
 * 一天沙箱起得怎么样。
 *
 * 这一层是容器：取数、定时重拉。画面在 `AdminMachinesPageView.vue`。看的是此刻，所以每
 * 30 秒自己重拉一次（标签页不可见时不拉）；重拉不清掉已有的数，画面只在还没有数据时
 * 画骨架。
 */
import type { MachinesOverview } from '@/views/admin/machinesApi'

import { onBeforeUnmount, onMounted, ref } from 'vue'

import AdminMachinesPageView from '@/views/admin/AdminMachinesPageView.vue'
import { getMachines } from '@/views/admin/machinesApi'

defineOptions({ name: 'AdminMachinesPage' })

const overview = ref<MachinesOverview | null>(null)
const loading = ref(true)
const failed = ref(false)

let generation = 0
async function load() {
  const mine = ++generation
  loading.value = true
  failed.value = false
  try {
    const next = await getMachines()
    if (mine === generation) overview.value = next
  } catch {
    if (mine === generation) failed.value = true
  } finally {
    if (mine === generation) loading.value = false
  }
}

const POLL_MS = 30_000
let pollTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void load()
  pollTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void load()
  }, POLL_MS)
})
onBeforeUnmount(() => clearInterval(pollTimer))
</script>

<template>
  <AdminMachinesPageView :overview="overview" :loading="loading" :failed="failed" @retry="load" />
</template>
