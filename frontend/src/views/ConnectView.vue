<script setup lang="ts">
// 设备审批页 (P3 Phase B item 3): the frozen cli's device flow opens
// `<origin>/connect?code=…`; the signed-in human lands here, optionally picks a
// project, and approves — which binds this machine to them as owner and mints its
// agent-user. App.vue only mounts the router when signed in, so `me` is guaranteed.
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { connectDevice, listProjects } from '../api'
import type { DeviceApproval, Project } from '../types'

const route = useRoute()
const code = computed(() => String(route.query.code ?? ''))

const projects = ref<Project[]>([])
const projectId = ref<string | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const approved = ref<DeviceApproval | null>(null)

async function load() {
  try {
    projects.value = (await listProjects()).data
  } catch {
    // Listing projects is best-effort — you can still approve without a project.
  }
}

async function approve() {
  if (!code.value) {
    error.value = '缺少设备码（请从 cli 打开的链接进入）'
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value, projectId.value ?? undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '审批失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
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

      <v-card v-if="!approved" class="pa-4">
        <div class="t-caption c-muted mb-1">设备码</div>
        <div class="device-code mb-4">{{ code || '—' }}</div>

        <v-select
          v-model="projectId"
          :items="projects"
          item-title="name"
          item-value="id"
          label="绑定到项目（可选）"
          density="comfortable"
          variant="outlined"
          clearable
          class="mb-2"
        />

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
          批准并绑定
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
