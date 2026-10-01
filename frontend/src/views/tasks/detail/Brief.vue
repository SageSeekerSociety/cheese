<template>
  <BriefView
    :task-data="taskData"
    :attachments="attachments"
    :can-download="canDownload"
    :downloading-id="downloadingId"
    @download="onDownload"
  />
</template>

<script setup lang="ts">
// 「说明」页签：取材料清单、处理下载，画面交给 `BriefView.vue`。
import type { TaskAttachmentData } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { watch } from 'vue'

import BriefView from './BriefView.vue'

import { useTaskAttachments } from '@/views/tasks/composables'

const props = defineProps<{
  taskData: Task | null
}>()

const { attachments, canDownload, downloadingId, load, download } = useTaskAttachments()

function onDownload(file: TaskAttachmentData) {
  if (props.taskData) download(props.taskData.id, file)
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
