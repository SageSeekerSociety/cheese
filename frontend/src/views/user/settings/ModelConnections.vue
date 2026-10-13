<script setup lang="ts">
/**
 * 「模型连接」（`/users/settings/model-connections`，#2991）。取数和动作在这里，画法在同目录的
 * `ModelConnectionsView.vue`——这一页只做它的容器。浏览器和桌面端里都有。
 */
import type { ModelConnection, ModelServiceDevice } from '@/types/modelConnections'
import type { ModelServiceInput } from '@/types/ownAgents'

import { onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { listMyDevices } from '@/api'
import { listModelConnections, revokeModelConnection, setModelService } from '@/api/modelConnections'
import { checkClaudeCode } from '@/api/ownAgents'
import { t } from '@/i18n'
import ModelConnectionsView from '@/views/user/settings/ModelConnectionsView.vue'

const connections = ref<ModelConnection[]>([])
const devices = ref<ModelServiceDevice[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref('')
const saved = ref(0)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [mine, enrolled] = await Promise.all([listModelConnections(), listMyDevices()])
    connections.value = mine.connections
    // 自己接入的设备（云端机器没有 claude_code 这一项），此刻在线的才能当场改。
    devices.value = enrolled.devices
      .filter((d) => d.online && 'claude_code' in d)
      .map((d) => ({ device_id: d.device_id, name: d.name }))
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('account.modelConnections.loadFailed')
  } finally {
    loading.value = false
  }
}

// 每个动作的结果跟这一次点击走，用全局 toast 说（§3.11）；做完重新取一遍列表，
// 一份连接被解除、改了服务或不再登录，以服务器说的为准。
async function act(key: string, fn: () => Promise<unknown>, done?: string): Promise<boolean> {
  busy.value = key
  try {
    await fn()
    if (done) toast.success(done)
    return true
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t('account.modelConnections.actionFailed'))
    return false
  } finally {
    busy.value = ''
    await load()
  }
}

function recheck(deviceId: string) {
  void act(`${deviceId}:check`, () => checkClaudeCode(deviceId))
}

function revoke(deviceId: string) {
  // 设备不在线或这一次没能退出登录，服务器把它记为「解除中」，设备连上后自动退出。
  void act(`${deviceId}:revoke`, async () => {
    const after = await revokeModelConnection(deviceId)
    toast.success(
      after?.revoking ? t('account.modelConnections.removedPending') : t('account.modelConnections.removed')
    )
  })
}

async function saveService(deviceId: string, service: ModelServiceInput) {
  if (await act('service', () => setModelService(deviceId, service), t('account.modelConnections.saved'))) {
    saved.value += 1
  }
}

onMounted(load)
</script>

<template>
  <ModelConnectionsView
    :loading="loading"
    :error="error"
    :connections="connections"
    :devices="devices"
    :busy="busy"
    :saved="saved"
    @recheck="recheck"
    @revoke="revoke"
    @service="saveService"
  />
</template>
