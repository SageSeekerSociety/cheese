<script setup lang="ts">
// 设备审批页 (P3 Phase B item 3): the frozen cli's device flow opens
// `<origin>/connect?code=…`; the signed-in human lands here and approves —
// which binds this machine to them as owner. A device is PURE COMPUTE (算力节点,
// execution-architecture v3) — approving does NOT mint an agent; which agent runs
// on it is decided per session/project later. Approval also deliberately does NOT
// ask which project the machine serves: a machine belongs to *you*, and which
// project uses its compute is decided later (device page / when a session picks it).
//
// 容器：读地址里的 code、预填 cli 提上来的名字、调后端批准。画面在
// `ConnectViewView.vue`，只收 props、只发 `approve`。
import type { DeviceApproval } from '../cx_types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { authToken, connectDevice, deviceProposedName } from '../api'
import { t } from '../i18n'

import ConnectViewView from './ConnectViewView.vue'

const route = useRoute()
const code = computed(() => String(route.query.code ?? ''))
// Approving binds the machine to a real account, so a token is required. If the
// human landed here from the cli link without a live session, send them to log
// in; the router brings them straight back here afterwards.
const loggedIn = computed(() => !!authToken())

const loading = ref(false)
const error = ref<string | null>(null)
const approved = ref<DeviceApproval | null>(null)
// The compute-node name, prefilled with the name the cli proposed (this machine's
// hostname) so the human sees a real default and can just edit it — never "unnamed".
const deviceName = ref('')

// Prefill from the cli-proposed name (the device's hostname). Best-effort: if the
// lookup fails the field stays blank and the server still falls back to a real name.
onMounted(async () => {
  if (!code.value) return
  try {
    const r = await deviceProposedName(code.value)
    if (r.device_name) deviceName.value = r.device_name
  } catch {
    // leave blank; approval still names the node from the server-side default
  }
})

async function approve(name: string) {
  if (!code.value) {
    error.value = t('project.connect.missingCode')
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value, name.trim() || undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('project.connect.failed')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <ConnectViewView
    :code="code"
    :logged-in="loggedIn"
    :loading="loading"
    :error="error"
    :approved="approved"
    :proposed-name="deviceName"
    @approve="approve"
  />
</template>
