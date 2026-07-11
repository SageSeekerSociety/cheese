<script setup lang="ts">
// 「我的设备 / Agent」(P3 Phase B item 4): the machines the signed-in human enrolled
// via the device flow. List them with liveness, rename/unbind, and open the 现场 of any
// agent (screen) currently running on them — a read-only real terminal in the browser.
import { onMounted, ref } from 'vue'
import {
  listMyDevices,
  renameMyDevice,
  unbindMyDevice,
} from '../api'
import type { DeviceScreen, MyDevice } from '../cx_types'
import DeviceLiveViewer from '../components/DeviceLiveViewer.vue'

const devices = ref<MyDevice[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// The screen whose 现场 is open in the viewer dialog.
const liveScreen = ref<DeviceScreen | null>(null)

// Inline rename state, keyed by device_id.
const renaming = ref<string | null>(null)
const draftName = ref('')

async function load() {
  loading.value = true
  error.value = null
  try {
    devices.value = (await listMyDevices()).devices
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设备失败'
  } finally {
    loading.value = false
  }
}

function startRename(d: MyDevice) {
  renaming.value = d.device_id
  draftName.value = d.name
}

async function saveRename(d: MyDevice) {
  const name = draftName.value.trim()
  if (!name || name === d.name) {
    renaming.value = null
    return
  }
  try {
    const updated = await renameMyDevice(d.device_id, name)
    Object.assign(d, updated)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重命名失败'
  } finally {
    renaming.value = null
  }
}

async function unbind(d: MyDevice) {
  if (!confirm(`确定解绑设备「${d.name}」吗？它的登录令牌将失效。`)) return
  try {
    await unbindMyDevice(d.device_id)
    devices.value = devices.value.filter((x) => x.device_id !== d.device_id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解绑失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="devices-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="mb-6 d-flex align-center">
        <div>
          <div class="t-eyebrow mb-1">连接器</div>
          <h1 class="t-page-title">我的设备</h1>
        </div>
        <v-spacer />
        <v-btn variant="text" icon="mdi-refresh" @click="load" />
      </div>

      <v-alert
        v-if="error"
        type="error"
        density="comfortable"
        class="mb-4"
        closable
        @click:close="error = null"
      >
        {{ error }}
      </v-alert>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <div v-else-if="devices.length === 0" class="empty-state text-center py-10">
        <v-icon size="34" class="mb-2 c-muted">mdi-laptop</v-icon>
        <div class="t-body c-muted">
          还没有连接的设备。在你的机器上运行 <code>cheese link</code> 并批准即可接入。
        </div>
      </div>

      <v-card
        v-for="d in devices"
        :key="d.device_id"
        class="mb-3 pa-4"
        variant="outlined"
      >
        <div class="d-flex align-center">
          <v-icon
            :color="d.online ? 'success' : 'grey'"
            size="12"
            class="mr-2"
          >
            mdi-circle
          </v-icon>

          <template v-if="renaming === d.device_id">
            <v-text-field
              v-model="draftName"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              style="max-width: 260px"
              @keyup.enter="saveRename(d)"
              @blur="saveRename(d)"
            />
          </template>
          <template v-else>
            <span class="t-title">{{ d.name }}</span>
            <v-btn
              variant="text"
              size="x-small"
              icon="mdi-pencil"
              class="ml-1"
              @click="startRename(d)"
            />
          </template>

          <v-spacer />
          <span class="t-caption c-muted mr-3">
            {{ d.online ? '在线' : '离线' }}
          </span>
          <v-btn
            variant="text"
            size="small"
            color="error"
            @click="unbind(d)"
          >
            解绑
          </v-btn>
        </div>

        <div class="t-caption c-muted mt-1">
          agent 身份:
          <strong v-if="d.agent_handle">@{{ d.agent_handle }}</strong>
          <span v-else>—</span>
        </div>

        <div v-if="d.screens.length" class="mt-3">
          <div class="t-caption c-muted mb-1">运行中的 agent 现场</div>
          <div class="d-flex flex-wrap ga-2">
            <v-chip
              v-for="s in d.screens"
              :key="s.sid"
              color="primary"
              variant="tonal"
              size="small"
              @click="liveScreen = s"
            >
              <v-icon start size="14">mdi-monitor-eye</v-icon>
              看现场 · @{{ s.agent_handle }}
            </v-chip>
          </div>
        </div>
      </v-card>
    </v-container>

    <v-dialog
      :model-value="liveScreen !== null"
      max-width="900"
      @update:model-value="liveScreen = null"
    >
      <v-card v-if="liveScreen" class="pa-3">
        <div class="d-flex align-center mb-2">
          <span class="t-title">现场 · @{{ liveScreen.agent_handle }}</span>
          <v-spacer />
          <v-btn variant="text" icon="mdi-close" @click="liveScreen = null" />
        </div>
        <div style="height: 60vh">
          <DeviceLiveViewer :sid="liveScreen.sid" />
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>
