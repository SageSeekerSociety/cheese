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
  failed: '准备失败',
  stopped: '芝士已停止',
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
    notice.value = '已保存。新房间使用这份配置；已有房间保持当前版本。'
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
      <span class="page-section-title">运行环境</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
      <v-alert v-if="notice" type="success" variant="tonal" class="mb-3">{{ notice }}</v-alert>
      <v-progress-linear v-if="!info && !error" indeterminate />
      <v-btn v-if="!info && error" variant="text" @click="load">重新加载</v-btn>
      <template v-if="info">
        <p class="t-body c-muted mb-3">
          为这个项目安装工具和依赖，让芝士开始工作前做好准备。工作房间共用这份配置；总览使用基础环境，协助处理环境故障。
        </p>
        <v-textarea
          v-model="setup"
          autocomplete="off"
          label="安装工具（初始化脚本）"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          hint="为房间安装 Python、Node 等工具。首次准备或应用新配置时运行"
          persistent-hint
          class="mb-4"
        />
        <v-textarea
          v-model="startup"
          autocomplete="off"
          label="准备项目（启动脚本）"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          hint="芝士每次重新启动前运行，安装当前分支的依赖；重新连接不会重跑"
          persistent-hint
          class="mb-4"
        />
        <details class="t-body c-muted mb-3">
          <summary>运行说明</summary>
          <p>Cloud、Hosted Machine 使用相同配置方式；Hosted Sandbox 暂未开放。机器需要支持脚本中的命令。</p>
          脚本以 Bash 在房间仓库目录运行，每段最多 30 分钟；使用机器当前权限。环境变量同时传给两个脚本和 AI
          进程，脚本里的 export 不会传给下一步。 工具可安装到 $HOME/.local/bin。
        </details>
        <p class="t-body mb-2">环境变量</p>
        <p class="t-body c-muted mb-3">这些值对项目成员和芝士可见。请不要在这里保存密码或 API 密钥。</p>
        <div v-for="(row, index) in variables" :key="index" class="d-flex align-start ga-2 mb-2">
          <v-text-field
            v-model="row.key"
            autocomplete="off"
            label="名称"
            variant="outlined"
            density="compact"
            :readonly="!info.can_edit"
          />
          <v-textarea
            v-model="row.value"
            autocomplete="off"
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
          <v-btn color="primary" :loading="saving" @click="save">保存配置</v-btn>
        </div>
        <p class="t-body c-muted mb-3">保存只影响新房间，已有房间保持当前配置</p>
        <details class="t-body c-faint mb-4">
          <summary>已保存的配置</summary>
          版本：{{ info.config.revision.slice(0, 12) }}
        </details>
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
            {{ labels[status.state]
            }}<span v-if="status.stage">
              · {{ status.stage === 'setup' ? '安装工具' : status.stage === 'startup' ? '准备项目' : '脚本完成' }}</span
            >
          </p>
          <p v-if="status?.error" class="t-body mb-2">
            {{ status.error }}<span v-if="status.exit_code != null">（退出码 {{ status.exit_code }}）</span>
          </p>
          <details v-if="status" class="t-body c-muted mb-2">
            <summary>配置与执行详情</summary>
            <p>版本：{{ status.pinned_revision?.slice(0, 12) ?? '尚未绑定' }}</p>
            <p v-if="status.started_at">
              开始：{{ new Date(status.started_at).toLocaleString() }}
              <span v-if="status.finished_at"> · 结束：{{ new Date(status.finished_at).toLocaleString() }}</span>
            </p>
            <p>日志显示最近 32 KB，历史日志保存在该房间 HOME 下</p>
          </details>
          <p v-if="status?.recovery_state === 'requested'" class="t-body mb-2">
            已交给总览芝士检查，可在总览查看处理情况
          </p>
          <p v-else-if="status?.recovery_state === 'retrying'" class="t-body mb-2">
            总览芝士已修正环境配置，正在重新启动
          </p>
          <p v-else-if="status?.recovery_state === 'needs_help'" class="t-body mb-2">
            自动处理未能恢复环境，请在总览查看需要的协助
          </p>
          <div class="d-flex flex-wrap ga-2 mb-3">
            <v-btn variant="text" @click="refreshStatus">刷新状态</v-btn>
            <v-btn
              v-if="info.can_edit"
              variant="outlined"
              :loading="applying"
              :disabled="saving || status?.busy || status?.state === 'preparing'"
              @click="apply(true)"
              >下次启动时应用</v-btn
            >
            <v-btn
              v-if="info.can_edit && status?.state === 'failed'"
              variant="text"
              :disabled="applying || status?.busy"
              @click="apply(false)"
              >下次启动时重试</v-btn
            >
          </div>
          <p class="t-body c-faint mb-2">房间文件会保留。正在工作的房间需要等当前工作结束后才能应用配置。</p>
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
  font-size: 13px;
}
</style>
