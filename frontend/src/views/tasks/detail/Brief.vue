<template>
  <BriefView
    :task-data="taskData"
    :attachments="attachments"
    :can-download="canDownload"
    :downloading="downloading"
    :progress="progress ?? null"
    @download="onDownload"
    @open="previewing = $event"
  />
  <TaskFilePreview
    :file="previewing"
    :read="readPreviewing"
    :downloading="previewing !== null && downloading === previewing.name"
    @close="previewing = null"
    @download="previewing && onDownload(previewing)"
  />
</template>

<script setup lang="ts">
// 「说明」页签：取材料清单、处理下载和点开预览，画面交给 `BriefView.vue`。
import type { Task, TaskProgress } from '@/types'
import type { TaskFileData } from '@/types/taskBoard'

import { ref, watch } from 'vue'

import BriefView from './BriefView.vue'

import { taskFileBytes } from '@/api/taskFiles'
import TaskFilePreview from '@/components/tasks/TaskFilePreview.vue'
import { useTaskAttachments } from '@/views/tasks/composables'

const props = defineProps<{
  taskData: Task | null
  /** 每个阶段上的进度，题目页取的那一份。 */
  progress?: TaskProgress | null
}>()

const { attachments, canDownload, downloading, load, download } = useTaskAttachments()

/** 点开预览的那一份。 */
const previewing = ref<TaskFileData | null>(null)

function readPreviewing(asPdf: boolean): Promise<ArrayBuffer> {
  if (!props.taskData || !previewing.value) return Promise.reject(new Error(''))
  return taskFileBytes(props.taskData.id, previewing.value.name, asPdf)
}

function onDownload(file: TaskFileData) {
  if (props.taskData) download(props.taskData.id, file)
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
