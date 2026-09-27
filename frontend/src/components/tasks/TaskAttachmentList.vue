<template>
  <v-card v-if="attachments.length > 0 || loading" flat rounded="lg" class="mt-4 task-info-card" border="sm">
    <v-card-item>
      <template #prepend>
        <div class="me-3">
          <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
            <v-icon color="primary" size="28">mdi-paperclip</v-icon>
          </v-avatar>
        </div>
      </template>
      <v-card-title class="text-h5 ps-0">题目附件</v-card-title>
    </v-card-item>

    <v-card-text>
      <v-progress-linear v-if="loading" indeterminate color="primary" />

      <v-list v-else density="compact" class="bg-surface-light rounded-lg">
        <v-list-item v-for="file in attachments" :key="file.id" data-testid="task-attachment">
          <template #prepend>
            <v-icon size="small" class="mr-2">mdi-file-outline</v-icon>
          </template>
          <v-list-item-title class="text-body-2">{{ file.name }}</v-list-item-title>
          <v-list-item-subtitle class="text-caption">
            {{ formatFileSize(file.size) }} · 已下载 {{ file.downloadCount }} 次
          </v-list-item-subtitle>
          <template #append>
            <!-- 看得见清单不等于拿得到文件。这一行由服务端的 canDownload 决定，
                 前端不拿自己的角色去猜 —— 判据只有一份。 -->
            <v-btn
              v-if="canDownload"
              variant="tonal"
              color="primary"
              size="small"
              :loading="downloadingId === file.id"
              @click="download(file)"
            >
              <v-icon start>mdi-download</v-icon>
              下载
            </v-btn>
            <span v-else class="text-caption text-medium-emphasis">领取这道题之后才能下载</span>
          </template>
        </v-list-item>
      </v-list>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import type { TaskAttachmentData } from '@/network/api/tasks/types'

import { ref, watch } from 'vue'

import { formatFileSize } from '@/utils/materials'

import { downloadFile, taskAttachmentRawUrl } from '@/api'
import { TasksApi } from '@/network/api/tasks'

/**
 * 一道题的材料清单。
 *
 * 清单本身对**看得见这道题的人**都可见：看不见材料就无从判断要不要领这道题。能不能
 * 下载是另一回事，由服务端在 `canDownload` 里给 —— 出题人、板管理员、已经领取的人
 * 拿得到，其余人那一行写「领取这道题之后才能下载」。
 */
const props = defineProps<{
  taskId: number | null | undefined
}>()

const attachments = ref<TaskAttachmentData[]>([])
const canDownload = ref(false)
const loading = ref(false)
const downloadingId = ref<number | null>(null)

const load = async () => {
  const taskId = props.taskId
  if (!taskId) {
    attachments.value = []
    canDownload.value = false
    return
  }

  loading.value = true
  try {
    const { data } = await TasksApi.listAttachments(taskId)
    attachments.value = data.attachments ?? []
    canDownload.value = data.canDownload ?? false
  } catch {
    // 清单取不到就整块不显示：一道没有材料的题和一次失败的请求在屏幕上长得一样，
    // 但把「加载失败」画成「没有附件」会让人以为材料不存在。这里选择不显示。
    attachments.value = []
    canDownload.value = false
  } finally {
    loading.value = false
  }
}

const download = async (file: TaskAttachmentData) => {
  if (!props.taskId) return
  downloadingId.value = file.id
  try {
    await downloadFile(taskAttachmentRawUrl(props.taskId, file.id), file.name)
    // 下载计数由服务端在真的取到字节之后 +1，这里跟着走一格，不重新拉整张清单。
    file.downloadCount += 1
  } finally {
    downloadingId.value = null
  }
}

watch(() => props.taskId, load, { immediate: true })
</script>
