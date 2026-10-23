<script setup lang="ts">
// 改动面板里「打开着的那一份文件」的正文：文档、逐文件 diff、图、放不下的二进制、
// 或者编辑器，五选一。
//
// 从 `PanelChangesView` 里分出来的（那个文件到了行数上限）。这一支只认「哪一份、以
// 什么形态看」，和另外两面（顶部横条、全部改动）没有关系。
import type { DocumentRevisionsBundle } from '../../composables/useDocumentRevisions'
import type { DiffLine } from '../../lib/diff'
import type { FileKind } from '../../lib/fileKind'
import type { OfficeComparison, ReviewBundle } from '../../types/reviewComment'

import { useDisplay } from 'vuetify'

import { fmtBytes } from '../../lib/changesTree'
import CodeEditor from '../CodeEditor.vue'
import ReviewDocument from '../review/ReviewDocument.vue'

import ChangesDiff from './ChangesDiff.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  openPath: string | null
  openIsDocument: boolean
  docLoading: boolean
  docBytes: ArrayBuffer | null
  docError: string
  docRendererMissing: boolean
  openDocumentType: FileKind | null
  /** 这一份 .docx 的修订：清单、只读、处理动作都在里面（`useDocumentRevisions.ts`）。 */
  revs: DocumentRevisionsBundle
  /** 修订只长在 .docx 上，别的时候这里给的是 null。 */
  revisionPath: string | null
  /** 这件任务的批注；宿主不给就不画（`composables/useReviewComments.ts`）。 */
  review?: ReviewBundle | null
  /** 打开的 Office 文件和上一版的比较（`usePanelChanges` 的那几个）。 */
  canCompare?: boolean
  comparing?: boolean
  comparison?: OfficeComparison | null
  comparisonLoading?: boolean
  comparisonError?: string
  effectiveView: 'diff' | 'edit'
  openDiffLines: DiffLine[]
  openIsImage: boolean
  openImageSrc: string
  /** 图片取不到时的原因；空串就是没出错。 */
  openImageError: string
  fileBinary: boolean
  fileTooLarge: boolean
  fileBytes: number
  fileDraft: string
  fileReadOnly: boolean
}>()

const emit = defineEmits<{
  download: []
  save: []
  'draft-changed': [content: string]
  'toggle-compare': []
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <!-- A document: render this revision, with the revisions it carries listed
     beside it. It comes before the diff because a .docx diff is the one line
     "binary file differs". -->
  <div v-if="props.openPath && props.openIsDocument" class="doc-view">
    <div v-if="props.docLoading && !props.docBytes" class="file-blob">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div v-else-if="props.docRendererMissing && !props.docBytes" class="file-blob">
      <v-icon size="30" class="c-faint mb-2">mdi-eye-off-outline</v-icon>
      <div class="file-blob__title">{{ t('work.room.changes.docPreviewDisabled') }}</div>
      <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
        <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
        {{ t('work.room.changes.downloadOriginal') }}
      </BaseButton>
    </div>
    <div v-else-if="props.docError && !props.docBytes" class="file-blob">
      <v-icon size="30" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div class="file-blob__title">{{ t('work.room.changes.cantDisplay') }}</div>
      <div class="file-blob__note">{{ props.docError }}</div>
      <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
        <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
        {{ t('work.room.changes.downloadOriginal') }}
      </BaseButton>
    </div>
    <ReviewDocument
      v-else
      class="doc-view__body"
      :path="props.openPath"
      :document-type="props.openDocumentType"
      :doc-bytes="props.docBytes"
      :revs="props.revs"
      :revision-path="props.revisionPath"
      :review="props.review"
      :can-compare="props.canCompare"
      :comparing="props.comparing"
      :comparison="props.comparison"
      :comparison-loading="props.comparisonLoading"
      :comparison-error="props.comparisonError"
      @toggle-compare="emit('toggle-compare')"
      @comment="(d) => void props.review?.add(d)"
      @edit-comment="(id, b, s) => void props.review?.edit(id, b, s)"
      @remove-comment="(id) => void props.review?.remove(id)"
    />
  </div>
  <!-- Per-file diff: one section per file, additions and deletions coloured.
     A single bare diff block cannot be read or located by file, which is why
     the acceptance flow never stood up. Line numbers, wrapping and windowing
     all live in ChangesDiff. -->
  <ChangesDiff v-else-if="props.openPath && props.effectiveView === 'diff'" :lines="props.openDiffLines" />
  <!-- An image that could not be fetched says so and offers the bytes; an empty
       pane read as "still loading" forever. -->
  <div v-else-if="props.openPath && props.openIsImage && props.openImageError" class="file-blob">
    <v-icon size="30" class="c-faint mb-2">mdi-image-broken-variant</v-icon>
    <div class="file-blob__title">{{ t('work.room.changes.cantDisplay') }}</div>
    <div class="file-blob__note">{{ props.openPath }} · {{ props.openImageError }}</div>
    <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
      <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
      {{ t('work.room.changes.downloadOriginal') }}
    </BaseButton>
  </div>
  <div v-else-if="props.openPath && props.openIsImage" class="file-image-view">
    <img v-if="props.openImageSrc" :src="props.openImageSrc" :alt="props.openPath" />
  </div>
  <!-- Binary / oversized: no editor. Opening one in Monaco meant every
     byte utf-8 could not decode came back as U+FFFD, and the save button
     wrote the damage to disk. -->
  <div v-else-if="props.openPath && (props.fileBinary || props.fileTooLarge)" class="file-blob">
    <v-icon size="30" class="c-faint mb-2">
      {{ props.fileTooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}
    </v-icon>
    <div class="file-blob__title">
      {{ props.fileTooLarge ? t('work.room.changes.tooLarge') : t('work.room.changes.binary') }}
    </div>
    <div class="file-blob__note">{{ props.openPath }} · {{ fmtBytes(props.fileBytes) }}</div>
    <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
      <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
      {{ t('work.room.changes.downloadOriginal') }}
    </BaseButton>
  </div>
  <!-- Read-only on a phone: a soft keyboard with Monaco is not a combination
       worth saving; saying so plainly beats an editor nobody can use. -->
  <CodeEditor
    v-else-if="props.openPath"
    :model-value="props.fileDraft"
    :filename="props.openPath"
    :readonly="!mdAndUp || props.fileReadOnly"
    @update:model-value="emit('draft-changed', $event)"
    @save="emit('save')"
  />
</template>

<style scoped>
.file-blob {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 13px;
  color: var(--text);
}
.file-blob__note {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
  word-break: break-all;
}
/* 文档那一面：页面在左，修订柱在右，和预览那一格同一个排法。 */
.doc-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.doc-view__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
@media (max-width: 720px) {
  .doc-view__body {
    flex-direction: column;
  }
}
.file-image-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow: auto;
  background: conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0) 0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: var(--shadow-1);
  /* The container's checkerboard is what says "transparent"; the image itself
     sits on the panel surface so a PNG with alpha is not slammed onto a white
     slab in the dark theme (GitHub's image viewer does the same). */
  background: var(--surface);
}
</style>
