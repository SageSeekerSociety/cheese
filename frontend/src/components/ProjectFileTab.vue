<script setup lang="ts">
// 频道右侧的一格文件页签：把 `useProjectFile` 的取数接到 `ProjectFileView` 上。
import type { NavTarget } from '../lib/navTarget'

import { useProjectFile } from '../composables/useProjectFile'

import ProjectFileView from './panels/ProjectFileView.vue'

const props = defineProps<{
  projectId: string | null
  channelId: string | null
  path: string
  lines: { start: number; end: number } | null
}>()

const emit = defineEmits<{
  (e: 'open-task', taskId: string): void
  (e: 'navigate', to: NavTarget): void
}>()

// 「在资料库打开」：同一份文件在资料库 › 项目文件里，旁边是整棵目录树和它的改动记录。
function openInLibrary() {
  if (!props.projectId) return
  emit('navigate', {
    name: 'project-library-files',
    params: { projectId: props.projectId },
    query: { file: props.path },
  })
}

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
    :in-room="file.inRoom.value"
    :is-image="file.isImage.value"
    :is-document="file.isDocument.value"
    :document-type="file.documentType.value"
    :image-src="file.imageSrc.value"
    :image-error="file.imageError.value"
    :doc-bytes="file.docBytes.value"
    :doc-loading="file.docLoading.value"
    :doc-error="file.docError.value"
    :doc-renderer-missing="file.docRendererMissing.value"
    @open-task="emit('open-task', $event)"
    @download="void file.download()"
    @open-in-library="openInLibrary"
  />
</template>
