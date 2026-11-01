<template>
  <VersionsView
    :versions="versions"
    :can-download="canDownload"
    :loading="loading"
    :failed="error !== null"
    :failure-reason="loadFailureReason(error)"
    :focus="focus"
    :downloading="downloading"
    @download="onDownload"
    @open="(version, file) => (previewing = { version, file })"
    @retry="load(taskData?.id)"
  />
  <TaskFilePreview
    :file="previewShown"
    :read="readPreviewing"
    :downloading="
      previewing !== null && downloading?.version === previewing.version && downloading.name === previewing.file.name
    "
    @close="previewing = null"
    @download="previewing && onDownload(previewing.version, previewing.file)"
  />
</template>

<script setup lang="ts">
// 「历史版本」页签：取这道题的每一版、处理下载和点开预览（和「说明」页签同一个预览框），
// 画面交给 `VersionsView.vue`。
// 地址上的 `?version=N` 是要落到的那一版：项目里「题目更新」那一行、项目里的 agent
// 读到的那一段，都带着它指过来。
import type { Task } from '@/types'
import type { TaskVersionFileData } from '@/types/taskFiles'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import VersionsView from './VersionsView.vue'

import { taskFileBytes } from '@/api/taskFiles'
import TaskFilePreview from '@/components/tasks/TaskFilePreview.vue'
import { loadFailureReason } from '@/lib/loadFailure'
import { useTaskVersions } from '@/views/tasks/composables'

const props = defineProps<{ taskData: Task | null }>()

const route = useRoute()
const focus = computed(() => {
  const n = Number(route.query.version)
  return Number.isInteger(n) && n > 0 ? n : null
})

const { versions, canDownload, loading, error, downloading, load, download } = useTaskVersions()

/** 点开预览的那一份：哪一版的哪个文件。 */
const previewing = ref<{ version: number; file: TaskVersionFileData } | null>(null)
const previewShown = computed(() => {
  const shown = previewing.value
  return shown && shown.file.size !== null ? { name: shown.file.name, size: shown.file.size } : null
})

function readPreviewing(asPdf: boolean): Promise<ArrayBuffer> {
  const shown = previewing.value
  if (!props.taskData || !shown) return Promise.reject(new Error(''))
  return taskFileBytes(props.taskData.id, shown.file.name, asPdf, shown.version)
}

function onDownload(version: number, file: TaskVersionFileData) {
  if (props.taskData) void download(props.taskData.id, version, file)
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
