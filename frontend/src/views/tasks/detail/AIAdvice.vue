<template>
  <div>
    <!-- 这一页的建议是 AI 生成的；对话入口放在这一页里，它追问的就是下面这几段。 -->
    <div class="aa__head">
      <v-btn variant="outlined" size="small" prepend-icon="mdi-creation" @click="aiChat?.openGeneralChat()">
        {{ t('tasks.advice.chat') }}
      </v-btn>
    </div>

    <!-- 使用AIAdvicePanel组件，添加视觉容器 -->
    <AIAdvicePanel
      :task-id="taskData?.id || 0"
      :submitter-type="taskData?.submitterType || 'USER'"
      :loading="loading"
      :error="error"
      :advice="aiAdvice"
      @retry="fetchAIAdvice"
    />
  </div>
</template>

<script setup lang="ts">
import type { TaskAIAdvice } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { defineAsyncComponent, inject, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { TasksApi } from '@/network/api/tasks'

const AIAdvicePanel = defineAsyncComponent(() => import('@/components/tasks/AIAdvicePanel.vue'))

const props = defineProps<{
  taskData: Task | null
}>()

const { t } = useI18n()

/** 题目详情那一层 `provide('aiChat')` 的对话入口（`useAIChat`）。 */
const aiChat = inject<{ openGeneralChat: () => void } | null>('aiChat', null)

// 状态
const aiAdvice = ref<TaskAIAdvice | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const pollingTimer = ref<number | null>(null)

// 轮询方法
const startPolling = () => {
  if (pollingTimer.value) return

  pollingTimer.value = window.setInterval(async () => {
    try {
      if (!props.taskData?.id) return

      const { data: statusData } = await TasksApi.getAIAdviceStatus(props.taskData.id)

      if (statusData.status === 'COMPLETED') {
        // 状态完成后，获取建议内容
        const { data: adviceData } = await TasksApi.getAIAdvice(props.taskData.id)
        aiAdvice.value = adviceData
        loading.value = false
        stopPolling()
      } else if (statusData.status === 'FAILED') {
        // 生成失败
        error.value = '生成建议失败，请重试'
        loading.value = false
        stopPolling()
      }
    } catch (err: any) {
      error.value = err instanceof Error ? err.message : '获取建议失败'
      loading.value = false
      stopPolling()
    }
  }, 2000) // 每2秒轮询一次
}

const stopPolling = () => {
  if (pollingTimer.value) {
    clearInterval(pollingTimer.value)
    pollingTimer.value = null
  }
}

const fetchAIAdvice = async () => {
  if (!props.taskData?.id) return

  loading.value = true
  error.value = null

  try {
    // 先尝试获取现有的建议
    try {
      const { data: existingAdvice } = await TasksApi.getAIAdvice(props.taskData.id)
      if (existingAdvice) {
        aiAdvice.value = existingAdvice
        loading.value = false
        return
      }
    } catch (err) {
      // 如果没有现有建议，继续请求生成
    }

    // 请求生成新建议
    const { data: requestData } = await TasksApi.requestAIAdvice(props.taskData.id)

    if (requestData.status === 'FAILED') {
      error.value = '生成建议失败，请重试'
      loading.value = false
    } else if (requestData.status === 'COMPLETED') {
      // 如果已经生成完成，直接获取结果
      const { data: adviceData } = await TasksApi.getAIAdvice(props.taskData.id)
      aiAdvice.value = adviceData
      loading.value = false
    } else {
      // 开始轮询
      startPolling()
    }
  } catch (err: any) {
    error.value = err instanceof Error ? err.message : '获取建议失败'
    loading.value = false
  }
}

// 生命周期
onMounted(() => {
  fetchAIAdvice()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.aa__head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 16px;
}
</style>
