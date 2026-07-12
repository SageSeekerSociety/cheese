<script setup lang="ts">
// 设备审批页 (P3 Phase B item 3): the frozen cli's device flow opens
// `<origin>/connect?code=…`; the signed-in human lands here and approves —
// which binds this machine to them as owner and mints its agent-user. Approval
// deliberately does NOT ask which project the machine serves: a machine belongs
// to *you*, and which project/session actually uses its compute is decided later
// (on the device page, or when a session picks a machine), not up front.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { authToken, connectDevice } from '../api'
import type { DeviceApproval } from '../cx_types'

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

async function approve() {
  if (!code.value) {
    error.value = '缺少设备码（请从 cli 打开的链接进入）'
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value)
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
          你的机器请求作为 self-hosted 计算接入芝士。批准后会为它铸一个
          agent 身份，芝士的分身即可在这台机器上干活。
        </div>
      </div>

      <v-alert
        v-if="!code"
        type="warning"
        density="comfortable"
        class="mb-4"
      >
        链接里没有设备码。请从 cli（<code>cheese link</code>）打开的地址进入本页面。
      </v-alert>

      <v-alert
        v-else-if="!loggedIn"
        type="info"
        density="comfortable"
        class="mb-4"
      >
        请先登录你的账号，再批准这台设备归你所有。
        <template #append>
          <v-btn size="small" variant="tonal" :to="loginLink">去登录</v-btn>
        </template>
      </v-alert>

      <v-card v-else-if="!approved" class="pa-4">
        <div class="t-caption c-muted mb-1">设备码</div>
        <div class="device-code mb-4">{{ code || '—' }}</div>

        <v-alert
          v-if="error"
          type="error"
          density="compact"
          class="mb-3"
        >
          {{ error }}
        </v-alert>

        <v-btn
          color="primary"
          :loading="loading"
          :disabled="!code"
          block
          @click="approve"
        >
          批准并绑定到我
        </v-btn>
      </v-card>

      <v-card v-else class="pa-5 text-center">
        <v-icon size="40" color="success" class="mb-2">
          mdi-check-circle-outline
        </v-icon>
        <h2 class="t-title mb-1">设备已连接</h2>
        <div class="t-body c-muted mb-3">
          «{{ approved.device_name }}» 已绑定到你，agent 身份
          <strong>@{{ approved.agent_handle }}</strong>。
        </div>
        <div class="t-caption c-muted mb-4">
          回到 cli，它会自动完成登录并保持在线。
        </div>
        <v-btn variant="tonal" :to="{ name: 'my-devices' }">
          去「我的设备」
        </v-btn>
      </v-card>
    </v-container>
  </div>
</template>

<style scoped>
.device-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  word-break: break-all;
  background: rgba(0, 0, 0, 0.04);
  padding: 8px 10px;
  border-radius: 6px;
}
</style>
