<template>
  <BriefView
    :task-data="taskData"
    :attachments="attachments"
    :can-download="canDownload"
    :downloading="downloading"
    :progress="progress ?? null"
    @download="onDownload"
  />
</template>

<script setup lang="ts">
// 「说明」页签：取材料清单、处理下载，画面交给 `BriefView.vue`。
import type { Task, TaskProgress } from '@/types'
import type { TaskFileData } from '@/types/taskBoard'

import { watch } from 'vue'

import BriefView from './BriefView.vue'

import { useTaskAttachments } from '@/views/tasks/composables'

const props = defineProps<{
  taskData: Task | null
  /** 每个阶段上的进度，题目页取的那一份。 */
  progress?: TaskProgress | null
}>()

const { attachments, canDownload, downloading, load, download } = useTaskAttachments()

function onDownload(file: TaskFileData) {
  if (props.taskData) download(props.taskData.id, file)
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
