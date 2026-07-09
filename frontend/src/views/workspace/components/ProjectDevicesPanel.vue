<!-- 项目设备管理侧栏（飞书式）：从项目工作区右上角「设备」拉出、贴右侧滑入。
     列出已绑定到本项目的设备（含在线状态、其上的 agent）；任何项目成员可见。
     绑定/解绑/改名仅设备所有者本人可操作（服务端也会校验，这里只是提前隐藏按钮）。
     绑定我自己拥有的设备时，从「我的设备」里选一个尚未绑定本项目的。 -->
<template>
  <teleport to="body">
    <transition name="pdp-fade">
      <div v-if="open" class="pdp-backdrop" @click="close" />
    </transition>
    <transition name="pdp-slide">
      <aside v-if="open" class="pdp">
        <header class="pdp-head">
          <span class="pdp-head__title">项目设备</span>
          <v-spacer />
          <v-btn icon="mdi-close" size="small" variant="text" @click="close" />
        </header>

        <div class="pdp-body">
          <section class="pdp-sec">
            <div class="pdp-sec__title">
              设备 <span class="pdp-count">{{ devices.length }}</span>
              <v-spacer />
              <v-menu v-model="bindMenu" :close-on-content-click="false" location="bottom end" :z-index="2600">
                <template #activator="{ props: mp }">
                  <v-btn v-bind="mp" size="small" color="primary" variant="tonal">＋ 绑定设备</v-btn>
                </template>
                <v-card min-width="260" class="pdp-add">
                  <div v-if="!myUnboundDevices.length" class="pdp-empty">
                    {{ myDevicesLoading ? '加载中…' : '没有可绑定的设备——先在「我的 Agent」页用 install.sh 接入一台' }}
                  </div>
                  <div
                    v-for="d in myUnboundDevices"
                    :key="d.device_id"
                    class="pdp-row pdp-row--pick"
                    @click="bind(d)"
                  >
                    <v-icon icon="mdi-laptop" size="20" :color="d.online ? 'success' : undefined" />
                    <span class="pdp-name">{{ d.name || d.device_id }}</span>
                    <v-spacer />
                    <v-progress-circular v-if="binding === d.device_id" size="16" width="2" indeterminate />
                    <v-icon v-else icon="mdi-plus" size="small" />
                  </div>
                </v-card>
              </v-menu>
            </div>
            <div v-if="error" class="pdp-err">{{ error }}</div>

            <div v-for="d in devices" :key="d.device_id" class="pdp-row pdp-row--device">
              <v-icon icon="mdi-laptop" size="22" :color="d.online ? 'success' : undefined" />
              <div class="pdp-meta">
                <span class="pdp-name">{{ d.name || d.device_id }}</span>
                <span class="pdp-sub">{{ d.online ? '在线' : '离线' }} · {{ d.agents.length }} 个 agent</span>
              </div>
              <v-spacer />
              <v-btn
                v-if="d.owner_user_id === selfId"
                size="x-small"
                variant="text"
                color="error"
                :loading="busy === d.device_id"
                title="从本项目解绑（不影响设备本身）"
                @click="unbind(d)"
              >
                解绑
              </v-btn>
              <span v-else class="pdp-tag">非本人设备</span>
            </div>
            <div v-if="!devices.length && !loading" class="pdp-empty">暂无绑定到本项目的设备</div>
          </section>
        </div>
      </aside>
    </transition>
  </teleport>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'

import type { Device, ProjectDevice } from '@/network/api/agents'
import {
  assignDeviceToProject,
  listMyDevices,
  listProjectDevices,
  unassignDeviceFromProject,
} from '@/network/api/agents'
import { currentUserId } from '@/services/account'

const props = defineProps<{ open: boolean; projectId: number | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const devices = ref<ProjectDevice[]>([])
const loading = ref(false)
const error = ref('')
const busy = ref<string | null>(null)
const selfId = currentUserId

function close(): void {
  emit('update:open', false)
}

async function refresh(): Promise<void> {
  if (!props.projectId) return
  loading.value = true
  try {
    const { devices: fresh } = await listProjectDevices(props.projectId)
    devices.value = fresh
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设备失败'
  } finally {
    loading.value = false
  }
}

// ── 绑定设备（从我拥有且尚未绑定本项目的设备里选）──
const bindMenu = ref(false)
const myDevices = ref<Device[]>([])
const myDevicesLoading = ref(false)
const binding = ref<string | null>(null)
const myUnboundDevices = ref<Device[]>([])

function recomputeUnbound(): void {
  const bound = new Set(devices.value.map((d) => d.device_id))
  myUnboundDevices.value = myDevices.value.filter((d) => !bound.has(d.device_id))
}

async function loadMyDevices(): Promise<void> {
  myDevicesLoading.value = true
  try {
    const { devices: fresh } = await listMyDevices()
    myDevices.value = fresh
    recomputeUnbound()
  } catch {
    myDevices.value = []
  } finally {
    myDevicesLoading.value = false
  }
}
watch(bindMenu, (o) => {
  if (o) void loadMyDevices()
})

async function bind(d: Device): Promise<void> {
  if (!props.projectId) return
  binding.value = d.device_id
  error.value = ''
  try {
    await assignDeviceToProject(d.device_id, props.projectId)
    await refresh()
    recomputeUnbound()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '绑定失败'
  } finally {
    binding.value = null
  }
}

async function unbind(d: ProjectDevice): Promise<void> {
  if (!props.projectId) return
  if (!window.confirm(`解绑设备「${d.name || d.device_id}」？该设备将不能再运行本项目的 agent。`)) return
  busy.value = d.device_id
  error.value = ''
  try {
    await unassignDeviceFromProject(d.device_id, props.projectId)
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解绑失败'
  } finally {
    busy.value = null
  }
}

// ── 生命周期：打开时刷新，并轮询保持在线状态新鲜 ──
let timer = 0
watch(
  () => [props.open, props.projectId] as const,
  ([o]) => {
    if (o) void refresh()
  },
  { immediate: true }
)
timer = window.setInterval(() => {
  if (props.open) void refresh()
}, 5000)
onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.pdp-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 2390;
}
.pdp {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  width: 340px;
  max-width: 92vw;
  z-index: 2400;
  background: rgb(var(--v-theme-surface));
  border-left: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: -8px 0 28px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
}
.pdp-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 12px 8px 12px 16px;
  font-weight: 700;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pdp-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
}
.pdp-sec {
  margin-bottom: 18px;
}
.pdp-sec__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 6px 0;
}
.pdp-count {
  opacity: 0.5;
  font-weight: 400;
}
.pdp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 4px;
  border-radius: 6px;
}
.pdp-row--device:hover,
.pdp-row--pick:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.pdp-row--pick {
  cursor: pointer;
}
.pdp-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pdp-name {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pdp-sub {
  font-size: 11px;
  opacity: 0.55;
}
.pdp-tag {
  font-size: 11px;
  opacity: 0.5;
  flex: none;
}
.pdp-empty {
  font-size: 12px;
  opacity: 0.5;
  padding: 6px 4px;
}
.pdp-err {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  padding: 4px;
}
.pdp-add {
  padding: 8px;
}
.pdp-slide-enter-active,
.pdp-slide-leave-active {
  transition: transform 0.22s ease;
}
.pdp-slide-enter-from,
.pdp-slide-leave-to {
  transform: translateX(100%);
}
.pdp-fade-enter-active,
.pdp-fade-leave-active {
  transition: opacity 0.22s ease;
}
.pdp-fade-enter-from,
.pdp-fade-leave-to {
  opacity: 0;
}
</style>
