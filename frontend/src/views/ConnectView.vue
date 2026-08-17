<script setup lang="ts">
// 设备审批页 (P3 Phase B item 3): the frozen cli's device flow opens
// `<origin>/connect?code=…`; the signed-in human lands here and approves —
// which binds this machine to them as owner. A device is PURE COMPUTE (算力节点,
// execution-architecture v3) — approving does NOT mint an agent; which agent runs
// on it is decided per session/project later. Approval also deliberately does NOT
// ask which project the machine serves: a machine belongs to *you*, and which
// project uses its compute is decided later (device page / when a session picks it).
import type { DeviceApproval } from '../cx_types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { authToken, connectDevice, deviceProposedName } from '../api'

const route = useRoute()
const code = computed(() => String(route.query.code ?? ''))
// Approving binds the machine to a real account, so a token is required. If the
// human landed here from the cli link without a live session, send them to log
// in and come straight back (SignIn honours ?redirect).
const loggedIn = computed(() => !!authToken())
const loginLink = computed(() => ({
  name: 'SignIn',
  query: { redirect: route.fullPath },
}))

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

async function approve() {
  if (!code.value) {
    error.value = '缺少设备码，请从命令行打开的链接进入'
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value, deviceName.value.trim() || undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '审批失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="connect-page fill-height overflow-y-auto">
    <v-container class="py-8" style="max-width: 560px">
      <div class="mb-6">
        <div class="t-eyebrow mb-1">设备连接</div>
        <h1 class="t-page-title">批准这台设备</h1>
        <div class="t-body c-muted mt-1">
          这台机器请求接入芝士，成为归你所有的设备。批准后，芝士就可以把任务派到它上面运行。
        </div>
      </div>

      <v-alert v-if="!code" type="warning" density="comfortable" class="mb-4">
        链接里没有设备码，请从 <code>cheese link</code> 打开的地址进入
      </v-alert>

      <v-alert v-else-if="!loggedIn" type="info" density="comfortable" class="mb-4">
        请先登录，再批准这台设备归你所有
        <template #append>
          <v-btn size="small" variant="tonal" :to="loginLink">去登录</v-btn>
        </template>
      </v-alert>

      <v-card v-else-if="!approved" class="pa-4">
        <div class="t-caption c-muted mb-1">设备码</div>
        <div class="device-code mb-4">{{ code || '—' }}</div>

        <v-text-field
          v-model="deviceName"
          label="设备名称"
          placeholder="例如：andy-macbook"
          variant="outlined"
          density="comfortable"
          hide-details
          class="mb-3"
          :disabled="loading"
          @keyup.enter="approve"
        />

        <v-alert v-if="error" type="error" density="compact" class="mb-3">
          {{ error }}
        </v-alert>

        <v-btn color="primary" :loading="loading" :disabled="!code" block @click="approve"> 批准并绑定到我 </v-btn>
      </v-card>

      <v-card v-else class="pa-5 text-center">
        <v-icon size="40" color="success" class="mb-2"> mdi-check-circle-outline </v-icon>
        <h2 class="t-title mb-1">设备已连接</h2>
        <div class="t-body c-muted mb-3">
          设备「<strong>{{ approved.device_name }}</strong
          >」已绑定到你
        </div>
        <div class="t-caption c-muted mb-4">
          在命令行运行 <code>cheese link connect</code>，设备就会保持在线并接受任务
        </div>
        <v-btn variant="tonal" :to="{ name: 'my-devices' }"> 去「我的设备」 </v-btn>
      </v-card>
    </v-container>
  </div>
</template>

<style scoped>
.device-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  word-break: break-all;
  background: var(--fill);
  padding: 8px 10px;
  border-radius: 6px;
}
</style>
