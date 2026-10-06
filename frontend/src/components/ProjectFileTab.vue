<script setup lang="ts">
// 频道右侧的一格文件页签：把 `useProjectFile` 的取数接到 `ProjectFileView` 上。
import { useProjectFile } from '../composables/useProjectFile'

import ProjectFileView from './panels/ProjectFileView.vue'

const props = defineProps<{
  projectId: string | null
  channelId: string | null
  path: string
  lines: { start: number; end: number } | null
}>()

const emit = defineEmits<{ (e: 'open-task', taskId: string): void }>()

const file = useProjectFile({
  projectId: () => props.projectId,
  channelId: () => props.channelId,
  path: () => props.path,
})
</script>

<template>
  <ProjectFileView
    :path="props.path"
    :lines="props.lines"
    :loading="file.loading.value"
    :error="file.error.value"
    :missing="file.missing.value"
    :content="file.content.value"
    :bytes="file.bytes.value"
    :binary="file.binary.value"
    :too-large="file.tooLarge.value"
    :tasks="file.tasks.value"
    :is-image="file.isImage.value"
    :is-document="file.isDocument.value"
    :document-type="file.documentType.value"
    :raw-url="file.rawUrl.value"
    :doc-bytes="file.docBytes.value"
    :doc-loading="file.docLoading.value"
    :doc-error="file.docError.value"
    :doc-renderer-missing="file.docRendererMissing.value"
    @open-task="emit('open-task', $event)"
    @download="void file.download()"
  />
</template>
