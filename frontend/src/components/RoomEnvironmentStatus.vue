<script setup lang="ts">
import type { EnvironmentStatus } from '../cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { getRoomEnvironment } from '../api'

const props = defineProps<{ projectId: string; topicId: string }>()
const status = ref<EnvironmentStatus | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

watch(
  () => [props.projectId, props.topicId],
  () => {
    const current = ++generation
    clearTimeout(timer)
    status.value = null
    async function refresh() {
      try {
        const result = await getRoomEnvironment(props.projectId, props.topicId)
        if (current === generation) status.value = result
      } catch {
        // The room remains usable while its machine status is unavailable.
        if (current === generation) status.value = null
      } finally {
        if (current === generation) timer = setTimeout(refresh, 5000)
      }
    }
    void refresh()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  generation++
  clearTimeout(timer)
})
</script>

<template>
  <v-alert
    v-if="status && (status.state === 'preparing' || status.state === 'failed' || status.recovery_state === 'retrying')"
    :type="status.state === 'failed' ? 'error' : 'info'"
    variant="tonal"
    class="ma-3"
    role="status"
  >
    <template v-if="status.state === 'preparing'">
      <p>正在{{ status.stage === 'setup' ? '安装工具' : '准备项目' }}，完成后芝士会继续处理你的消息</p>
      <v-progress-linear indeterminate class="mt-2" />
    </template>
    <p v-else-if="status.recovery_state === 'retrying'">总览芝士已修正环境配置，正在重新启动</p>
    <template v-else>
      <p>环境准备失败，芝士还没有开始处理这条消息。</p>
      <p v-if="status.recovery_state === 'requested'">已交给总览芝士检查，可在总览查看处理情况。</p>
      <p v-else-if="status.recovery_state === 'needs_help'">自动处理未能恢复环境，请在总览查看需要的协助。</p>
      <p v-else>请查看安装日志，或在运行环境设置中修改配置并安排重试。</p>
    </template>
    <details v-if="status.log" class="mt-2">
      <summary>安装日志</summary>
      <pre class="environment-log">{{ status.log }}</pre>
    </details>
  </v-alert>
</template>

<style scoped>
.environment-log {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
}
</style>
