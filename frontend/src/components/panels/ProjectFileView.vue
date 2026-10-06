<script setup lang="ts">
// 频道里点一枚文件 chip 开的那一格：项目当前版本里的这份文件，只读。代码在编辑器里
// 打开，带行号就滚到那几行并选中；图片、文档照样画出来；画不出来的给下载。
//
// 当前版本里没有这份文件时，说清这一点，再列出这个频道里改过它的任务——它多半是某件
// 任务里新建、还没合进项目的。
//
// **只认 props**：取数在 `composables/useProjectFile.ts`，`ProjectFileTab.vue` 把它接上。
import type { RoomTask } from '../../cx_types'
import type { FileKind } from '../../lib/fileKind'

import { useDisplay } from 'vuetify'

import { fmtBytes } from '../../lib/changesTree'
import CodeEditor from '../CodeEditor.vue'

import PreviewPages from './preview/PreviewPages.vue'
import PreviewSheet from './preview/PreviewSheet.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  path: string
  lines: { start: number; end: number } | null
  loading: boolean
  error: string | null
  missing: boolean
  content: string
  bytes: number
  binary: boolean
  tooLarge: boolean
  /** 当前版本里没有这份文件时，这个频道里改过它的任务。 */
  tasks: RoomTask[]
  isImage: boolean
  isDocument: boolean
  documentType: FileKind | null
  rawUrl: string
  docBytes: ArrayBuffer | null
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
}>()

const emit = defineEmits<{
  (e: 'open-task', taskId: string): void
  (e: 'download'): void
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <div class="project-file" data-testid="project-file">
    <div class="project-file__bar">
      <span class="project-file__path" :title="props.path">
        {{ mdAndUp ? props.path : props.path.split('/').pop() }}
      </span>
      <v-spacer />
      <span class="project-file__source">{{ t('work.room.projectFile.current') }}</span>
    </div>
    <div v-if="props.loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <v-alert v-else-if="props.error" type="error" density="compact" class="ma-4">{{ props.error }}</v-alert>
    <div v-else-if="props.missing" class="project-file__missing" data-testid="project-file-missing">
      <p class="t-body">{{ t('work.room.projectFile.missing') }}</p>
      <button
        v-for="task in props.tasks"
        :key="task.id"
        type="button"
        class="project-file__task"
        @click="emit('open-task', task.id)"
      >
        <v-icon size="16">mdi-arrow-right</v-icon>
        <span>{{ t('work.room.projectFile.inTask', { title: taskTitle(task) }) }}</span>
      </button>
    </div>
    <div v-else-if="props.isDocument" class="project-file__body">
      <div v-if="props.docLoading && !props.docBytes" class="file-blob">
        <v-progress-circular indeterminate color="primary" size="24" />
      </div>
      <div v-else-if="(props.docRendererMissing || props.docError) && !props.docBytes" class="file-blob">
        <v-icon size="30" class="c-faint mb-2">mdi-file-alert-outline</v-icon>
        <div class="file-blob__title">
          {{
            props.docRendererMissing ? t('work.room.changes.docPreviewDisabled') : t('work.room.changes.cantDisplay')
          }}
        </div>
        <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
          {{ t('work.room.changes.downloadOriginal') }}
        </BaseButton>
      </div>
      <PreviewPages v-else-if="props.documentType?.view === 'pages'" :data="props.docBytes" />
      <PreviewSheet v-else :data="props.docBytes" :kind="props.documentType?.sheet ?? 'workbook'" />
    </div>
    <div v-else-if="props.isImage" class="project-file__image">
      <img :src="props.rawUrl" :alt="props.path" />
    </div>
    <div v-else-if="props.binary || props.tooLarge" class="file-blob">
      <v-icon size="30" class="c-faint mb-2">{{ props.tooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}</v-icon>
      <div class="file-blob__title">
        {{ props.tooLarge ? t('work.room.changes.tooLarge') : t('work.room.changes.binary') }}
      </div>
      <div class="file-blob__note">{{ props.path }} · {{ fmtBytes(props.bytes) }}</div>
      <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
        {{ t('work.room.changes.downloadOriginal') }}
      </BaseButton>
    </div>
    <div v-else class="project-file__body">
      <CodeEditor :model-value="props.content" :filename="props.path" :lines="props.lines" readonly />
    </div>
  </div>
</template>

<style scoped>
.project-file {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--surface);
}
.project-file__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}
.project-file__path {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-file__source {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.project-file__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}
.project-file__body > :deep(.code-editor) {
  flex: 1 1 auto;
}
.project-file__missing {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 16px;
}
.project-file__missing p {
  margin: 0;
  color: var(--text);
}
.project-file__task {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  cursor: pointer;
}
.project-file__task:hover {
  color: var(--ink);
  text-decoration: underline;
}
.project-file__image {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 16px;
}
.project-file__image img {
  max-width: 100%;
}
.file-blob {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 13px;
  color: var(--text);
}
.file-blob__note {
  margin-top: 4px;
  font-size: 13px;
  color: var(--muted);
  word-break: break-all;
}
</style>
