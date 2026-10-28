<script setup lang="ts">
// 一版产物长什么样：图片、文档、表格、幻灯片，或者只剩一个下载链接。
//
// 取这一版的字节、以及取到之后画成哪一种，在
// `composables/useArtifactVersionPreview.ts` 那一层：组件不直接碰 API 层
// （`.claude/rules/architecture.md` 的组件边界）。这里只管画。
import type { ArtifactVersion } from '../api'

import { useArtifactVersionPreview } from '../composables/useArtifactVersionPreview'
import { t } from '../i18n'
import { codeLanguageOf, sheetKindOf, SLIDE_SUFFIXES } from '../lib/fileKind'

import DesignImage from './panels/preview/DesignImage.vue'
import PreviewCode from './panels/preview/PreviewCode.vue'
import PreviewMarkdown from './panels/preview/PreviewMarkdown.vue'
import PreviewPages from './panels/preview/PreviewPages.vue'
import PreviewSheet from './panels/preview/PreviewSheet.vue'
import PreviewSlides from './panels/preview/PreviewSlides.vue'

const props = withDefaults(
  defineProps<{
    projectId: string
    artifactId: string
    version: ArtifactVersion
    /** 没有标题栏、填满父级：产物页上那一块预览就是它自己，不是并排的两张卡之一。 */
    bare?: boolean
  }>(),
  { bare: false }
)

const { data, imageUrl, text, loading, error, suffix, view } = useArtifactVersionPreview(() => ({
  projectId: props.projectId,
  artifactId: props.artifactId,
  version: props.version,
}))
</script>

<template>
  <section class="version-preview" :class="{ 'version-preview--bare': bare }">
    <header v-if="!bare" class="pa-3">
      <h3 class="t-title">{{ t('tasks.artifactComparison.version', { number: version.number }) }}</h3>
      <p class="t-body c-muted">{{ version.filename || version.subject }}</p>
    </header>
    <p v-if="loading" class="pa-4 t-body" role="status">{{ t('tasks.artifactComparison.loadingPreview') }}</p>
    <p v-else-if="error" class="pa-4 t-body c-danger" role="alert">{{ error }}</p>
    <a
      v-else-if="version.kind === 'link' && version.url"
      class="pa-4 t-body version-link"
      :href="version.url"
      target="_blank"
      rel="noopener noreferrer"
      >{{ version.url }}</a
    >
    <div v-else-if="data" class="version-preview__body">
      <PreviewSlides v-if="SLIDE_SUFFIXES.has(suffix)" :data="data" :title="version.filename || ''" />
      <PreviewPages v-else-if="view === 'pages'" :data="data" />
      <PreviewSheet v-else-if="view === 'sheet'" :data="data" :kind="sheetKindOf(suffix)" />
      <PreviewMarkdown v-else-if="view === 'markdown' && text !== null" :source="text" />
      <PreviewCode v-else-if="view === 'code' && text !== null" :source="text" :language="codeLanguageOf(suffix)" />
      <DesignImage
        v-else-if="imageUrl"
        :src="imageUrl"
        :alt="version.filename || ''"
        :identity="`${artifactId}:${version.card_id}`"
      />
      <pre v-else-if="text !== null" class="pa-3 t-body">{{ text }}</pre>
      <p v-else class="pa-4 t-body c-muted">{{ t('tasks.artifactComparison.downloadOnly') }}</p>
    </div>
    <p v-else class="pa-4 t-body c-muted">{{ t('tasks.artifactComparison.noPreview') }}</p>
  </section>
</template>

<style scoped>
.version-preview {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.version-preview header {
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
}
.version-preview__body {
  max-height: 640px;
  overflow: auto;
}
.version-preview--bare {
  display: flex;
  flex-direction: column;
  height: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.version-preview--bare .version-preview__body {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
}
.version-preview__body pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.version-preview__body img {
  display: block;
  max-width: 100%;
  height: auto;
}
.version-link {
  display: block;
  overflow-wrap: anywhere;
  color: var(--text);
}
</style>
