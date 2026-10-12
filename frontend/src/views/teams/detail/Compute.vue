<script setup lang="ts">
// The team's work computers: every self-hosted device its projects can use,
// whether registered for the team or attached to one of its projects. Cloud
// sandboxes are the platform's, and are not listed here.
import type { MyDevice } from '@/types/devices'

import { computed, inject, onMounted, ref, watch } from 'vue'

import ComputeView from './ComputeView.vue'

import { listMyDevices, listTeamDevices, registerDeviceForTeam, unregisterDeviceFromTeam } from '@/api'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { useDialog } from '@/plugins/dialog'

const { confirm } = useDialog()
const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)
// 自己名下（只有自己的那个团队）不说「团队」：说到归属的几句各有一份。
const scope = computed(() => (teamData.value?.personal ? 'own' : 'team'))

const devices = ref<MyDevice[]>([])
const myDevices = ref<MyDevice[]>([])
const loading = ref(false)
/** 写操作失败：一句话，可关，页面照旧（下面那块内容没受影响）。 */
const error = ref<string | null>(null)
/** 读失败：这一块内容根本没拿到，`devices` 空是假的。 */
const loadError = ref<unknown>(null)
const loadFailed = computed(() => loadError.value !== null)
// 「没读到」和「没权限」分开判，再交给那一半画——它不连网络层，也拿不到这个判定。
const loadFailureReasonText = computed(() => loadFailureReason(loadError.value))
const loadForbidden = computed(() => isForbidden(loadError.value))
const busy = ref<string | null>(null)

const myDeviceIds = computed(() => new Set(myDevices.value.map((device) => device.device_id)))
const addable = computed(() => myDevices.value.filter((device) => !device.team_ids.includes(teamId.value)))
const onlineCount = computed(() => devices.value.filter((device) => device.online).length)

function errorMessage(value: unknown, fallback: string): string {
  return value instanceof Error ? value.message : fallback
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [teamDevices, mine] = await Promise.all([
      listTeamDevices(teamId.value),
      listMyDevices().catch(() => ({ devices: [] })),
    ])
    devices.value = teamDevices.devices
    myDevices.value = mine.devices
  } catch (cause) {
    // 原来记在 `error` 里 —— 那条提示可关，关掉之后屏幕上只剩「还没有自己的机器」。
    loadError.value = cause
  } finally {
    loading.value = false
  }
}

async function addMachine(device: MyDevice) {
  busy.value = device.device_id
  error.value = null
  try {
    await registerDeviceForTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.addMachineFailed'))
  } finally {
    busy.value = null
  }
}

async function removeMachine(device: MyDevice) {
  const ok = await confirm(t(`teams.compute.${scope.value}.removeDeviceConfirm`, { name: device.name }), {
    danger: true,
    confirmLabel: t(`teams.compute.${scope.value}.remove`),
  }).wait()
  if (!ok) return
  busy.value = device.device_id
  error.value = null
  try {
    await unregisterDeviceFromTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.removeMachineFailed'))
  } finally {
    busy.value = null
  }
}

onMounted(load)
watch(teamId, load)
</script>

<template>
  <ComputeView
    v-model:error="error"
    :scope="scope"
    :loading="loading"
    :load-failed="loadFailed"
    :reason="loadFailureReasonText"
    :forbidden="loadForbidden"
    :addable="addable"
    :busy="busy"
    :my-devices="myDevices"
    :devices="devices"
    :online-count="onlineCount"
    :team-id="teamId"
    :my-device-ids="myDeviceIds"
    @load="load"
    @add-machine="addMachine"
    @remove-machine="removeMachine"
  />
</template>
