<script setup lang="ts">
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { applyRoomEnvironment, getProjectEnvironment, getRoomEnvironment, saveProjectEnvironment } from '../api'

const props = defineProps<{ projectId: string }>()
const info = ref<ProjectEnvironmentInfo | null>(null)
const setup = ref('')
const startup = ref('')
const variables = ref<{ key: string; value: string }[]>([])
const selectedRoom = ref<string | null>(null)
const status = ref<EnvironmentStatus | null>(null)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const applying = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let generation = 0
const labels: Record<EnvironmentStatus['state'], string> = {
  pending: '等待下次启动',
  preparing: '正在准备',
  ready: '已就绪',
  failed: '准备失败或进程已退出',
  offline: '机器离线',
}

async function load() {
  const current = ++generation
  error.value = ''
  info.value = null
  selectedRoom.value = null
  try {
    const result = await getProjectEnvironment(props.projectId)
    if (disposed || current !== generation) return
    info.value = result
    setup.value = result.config.setup_script
    startup.value = result.config.startup_script
    variables.value = Object.entries(result.config.variables).map(([key, value]) => ({ key, value }))
    selectedRoom.value = result.rooms[0]?.id ?? null
  } catch (e) {
    if (current === generation) error.value = e instanceof Error ? e.message : '读取环境失败'
  }
}

async function refreshStatus() {
  clearTimeout(timer)
  const room = selectedRoom.value
  const current = generation
  if (!room || disposed) return
  try {
    const result = await getRoomEnvironment(props.projectId, room)
    if (disposed || current !== generation || room !== selectedRoom.value) return
    status.value = result
  } catch (e) {
    if (current === generation && room === selectedRoom.value)
      error.value = e instanceof Error ? e.message : '读取安装状态失败'
  } finally {
    if (!disposed && current === generation && room === selectedRoom.value) timer = setTimeout(refreshStatus, 5000)
  }
}

async function save() {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const values: Record<string, string> = Object.create(null)
    for (const row of variables.value) {
      if (!row.key || Object.hasOwn(values, row.key)) throw new Error('环境变量名称不能为空或重复')
      values[row.key] = row.value
    }
    const config = await saveProjectEnvironment(props.projectId, {
      setup_script: setup.value,
      startup_script: startup.value,
      variables: values,
    })
    if (info.value) info.value.config = config
    notice.value = '已保存。新房间使用此配置；已有房间保持当前版本。'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存环境失败'
  } finally {
    saving.value = false
  }
}

async function apply(latest: boolean) {
  if (!selectedRoom.value) return
  applying.value = true
  error.value = ''
  notice.value = ''
  try {
    await applyRoomEnvironment(props.projectId, selectedRoom.value, latest)
    notice.value = '已安排在下次启动时准备环境，房间里的文件已保留。'
    await refreshStatus()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '应用环境失败'
  } finally {
    applying.value = false
  }
}

watch(() => props.projectId, load, { immediate: true })
watch(selectedRoom, () => {
  status.value = null
  void refreshStatus()
})
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
})
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-console</v-icon>
      <span class="page-section-title">项目环境</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
      <v-alert v-if="notice" type="success" variant="tonal" class="mb-3">{{ notice }}</v-alert>
      <v-progress-linear v-if="!info && !error" indeterminate />
      <v-btn v-if="!info && error" variant="text" @click="load">重新加载</v-btn>
      <template v-if="info">
        <p class="t-body c-muted mb-3">
          Cloud、Hosted Sandbox、Hosted Machine 共用此配置；机器仍需支持所用的命令。Hosted Sandbox 尚未开放。
        </p>
        <v-textarea
          v-model="setup"
          label="初始化脚本"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          hint="新环境或配置版本变化时运行一次。工具建议安装到 $HOME/.local/bin。"
          persistent-hint
          class="mb-4"
        />
        <v-textarea
          v-model="startup"
          label="启动脚本"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          hint="每次启动新的 AI 进程前运行，可安装当前分支的依赖；重连不会重跑。"
          persistent-hint
          class="mb-4"
        />
        <p class="t-body c-muted mb-3">
          脚本以 Bash 在房间仓库目录运行，每段最多 30 分钟；使用机器当前权限。环境变量同时传给两个脚本和 AI
          进程，脚本里的 export 不会传给下一步。
        </p>
        <p class="t-body mb-2">环境变量（普通配置，请勿填入密钥）</p>
        <div v-for="(row, index) in variables" :key="index" class="d-flex align-start ga-2 mb-2">
          <v-text-field
            v-model="row.key"
            label="名称"
            variant="outlined"
            density="compact"
            :readonly="!info.can_edit"
          />
          <v-textarea
            v-model="row.value"
            label="值"
            variant="outlined"
            density="compact"
            rows="1"
            auto-grow
            :readonly="!info.can_edit"
          />
          <v-btn
            v-if="info.can_edit"
            icon="mdi-close"
            variant="text"
            size="small"
            aria-label="删除环境变量"
            @click="variables.splice(index, 1)"
          />
        </div>
        <div v-if="info.can_edit" class="d-flex ga-2 mb-3">
          <v-btn variant="text" @click="variables.push({ key: '', value: '' })">添加变量</v-btn>
          <v-btn color="primary" :loading="saving" @click="save">保存环境</v-btn>
        </div>
        <p class="t-body c-faint mb-4">保存版本：{{ info.config.revision.slice(0, 12) }}。保存只影响新房间。</p>
        <template v-if="info.rooms.length">
          <v-select
            v-model="selectedRoom"
            :items="info.rooms"
            item-title="title"
            item-value="id"
            label="查看房间环境"
            variant="outlined"
            density="compact"
          />
          <p v-if="status" class="t-body mb-2">
            {{ labels[status.state] }} · 版本 {{ status.pinned_revision?.slice(0, 12) ?? '尚未绑定'
            }}<span v-if="status.stage">
              · {{ status.stage === 'setup' ? '初始化' : status.stage === 'startup' ? '启动脚本' : '脚本完成' }}</span
            >
          </p>
          <p v-if="status?.error" class="t-body mb-2">
            {{ status.error }}<span v-if="status.exit_code != null">（退出码 {{ status.exit_code }}）</span>
          </p>
          <p v-if="status?.started_at" class="t-body c-muted mb-2">
            开始：{{ new Date(status.started_at).toLocaleString() }}
            <span v-if="status.finished_at"> · 结束：{{ new Date(status.finished_at).toLocaleString() }}</span>
          </p>
          <div class="d-flex flex-wrap ga-2 mb-3">
            <v-btn variant="text" @click="refreshStatus">刷新状态</v-btn>
            <v-btn
              v-if="info.can_edit"
              variant="outlined"
              :loading="applying"
              :disabled="saving || status?.state === 'preparing'"
              @click="apply(true)"
              >下次启动应用已保存配置</v-btn
            >
            <v-btn
              v-if="info.can_edit && status?.state === 'failed'"
              variant="text"
              :disabled="applying"
              @click="apply(false)"
              >重试当前版本</v-btn
            >
          </div>
          <p class="t-body c-faint mb-2">
            应用配置会停止空闲房间的 AI 进程并保留文件；房间正在工作时不可应用。日志显示最近 32 KB，历史日志保存在该房间
            HOME 下。
          </p>
          <pre v-if="status?.log" class="environment-log">{{ status.log }}</pre>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.environment-log {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
</style>
