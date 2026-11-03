<script setup lang="ts">
import type { MyDevice } from '@/types/devices'

import { computed, inject, onMounted, ref, watch } from 'vue'

import ComputeView from './ComputeView.vue'

import { listTeamDevices, renameMyDevice, unbindMyDevice } from '@/api'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { desktopBridge, deviceFlow, requestConnecting } from '@/lib/desktop'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { useDialog } from '@/plugins/dialog'
import { keys } from '@/query/keys'
import { fromSnapshot } from '@/query/snapshot'

const { confirm } = useDialog()
const team = inject(teamDataInjectionKey, ref())
const teamId = computed(() => team.value?.id ?? 0)
const devices = ref<MyDevice[]>([])
const canManage = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const loadError = ref<unknown>(null)
const busy = ref<string | null>(null)
const desktop = !!desktopBridge()
const command = computed(
  () =>
    `curl -fsSL ${window.location.origin}/connector/install.sh | sh\ncheesehost --profile team-${teamId.value} link connect ${window.location.origin}/connector`
)
const windowsCommand = computed(
  () =>
    `irm ${window.location.origin}/connector/install.ps1 | iex\ncheesehost --profile team-${teamId.value} link connect ${window.location.origin}/connector`
)

let loadSeq = 0
async function load(fresh = false) {
  const id = teamId.value
  const seq = ++loadSeq
  if (!id) {
    devices.value = []
    canManage.value = false
    loading.value = false
    loadError.value = null
    return
  }
  loading.value = true
  loadError.value = null
  try {
    const read = () => listTeamDevices(id)
    const result = fresh ? await read() : await fromSnapshot(keys.teamDevices(id), read)
    if (seq !== loadSeq || teamId.value !== id) return
    devices.value = result.devices
    canManage.value = result.can_manage
  } catch (cause) {
    if (seq !== loadSeq || teamId.value !== id) return
    loadError.value = cause
  } finally {
    if (seq === loadSeq && teamId.value === id) loading.value = false
  }
}
async function rename(device: MyDevice, name: string) {
  if (!name.trim() || name.trim() === device.name) return
  // A message left from an earlier action must not stand for this attempt: the dialog closes only on a clean finish.
  error.value = null
  busy.value = device.device_id
  try {
    await renameMyDevice(device.device_id, name.trim())
    await load(true)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('account.devices.renameFailed')
  } finally {
    busy.value = null
  }
}
async function remove(device: MyDevice) {
  if (
    !(await confirm(t('teams.compute.removeConfirm', { name: device.name }), {
      danger: true,
      confirmLabel: t('teams.compute.remove'),
    }).wait())
  )
    return
  busy.value = device.device_id
  try {
    await unbindMyDevice(device.device_id)
    await load(true)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('teams.compute.removeMachineFailed')
  } finally {
    busy.value = null
  }
}
onMounted(() => void load())
watch(teamId, () => void load())
watch(
  () => deviceFlow.stage,
  (stage) => {
    if (stage === 'done') void load(true)
  }
)
</script>

<template>
  <ComputeView
    v-model:error="error"
    :devices="devices"
    :personal="!!team?.personal"
    :can-manage="canManage"
    :loading="loading"
    :load-failed="loadError !== null"
    :reason="loadFailureReason(loadError)"
    :forbidden="isForbidden(loadError)"
    :busy="busy"
    :desktop="desktop"
    :command="command"
    :windows-command="windowsCommand"
    @load="load(true)"
    @rename="rename"
    @remove-machine="remove"
    @connect="requestConnecting(teamId)"
  />
</template>
