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
    @retry="load(taskData?.id)"
  />
</template>

<script setup lang="ts">
// 「历史版本」页签：取这道题的每一版、处理下载，画面交给 `VersionsView.vue`。
// 地址上的 `?version=N` 是要落到的那一版：项目里「题目更新」那一行、项目里的 agent
// 读到的那一段，都带着它指过来。
import type { Task } from '@/types'
import type { TaskVersionFileData } from '@/types/taskFiles'

import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import VersionsView from './VersionsView.vue'

import { loadFailureReason } from '@/lib/loadFailure'
import { useTaskVersions } from '@/views/tasks/composables'

const props = defineProps<{ taskData: Task | null }>()

const route = useRoute()
const focus = computed(() => {
  const n = Number(route.query.version)
  return Number.isInteger(n) && n > 0 ? n : null
})

const { versions, canDownload, loading, error, downloading, load, download } = useTaskVersions()

function onDownload(version: number, file: TaskVersionFileData) {
  if (props.taskData) void download(props.taskData.id, version, file)
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
