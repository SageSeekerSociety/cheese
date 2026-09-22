<script setup lang="ts">
import type { ArtifactVersion } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { artifactVersionBytes } from '../api'
import { t } from '../i18n'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, NEEDS_CONVERSION, imageMimeOf, suffixOf } from '../lib/fileKind'

import PreviewPages from './panels/preview/PreviewPages.vue'
import PreviewSheet from './panels/preview/PreviewSheet.vue'

const props = defineProps<{ projectId: string; artifactId: string; version: ArtifactVersion }>()
const data = ref<ArrayBuffer | null>(null)
const imageUrl = ref('')
const text = ref<string | null>(null)
const loading = ref(false)
const error = ref('')
const suffix = computed(() => suffixOf(props.version.filename ?? ''))
const view = computed(() => DOCUMENT_TYPES[suffix.value]?.view)
let generation = 0

function clear() {
  data.value = null
  text.value = null
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}

watch(
  () => [props.projectId, props.artifactId, props.version.card_id],
  async () => {
    const current = ++generation
    clear()
    error.value = ''
    loading.value = false
    if (props.version.kind !== 'file') return
    loading.value = true
    try {
      const bytes = await artifactVersionBytes(
        props.projectId,
        props.artifactId,
        props.version.card_id,
        NEEDS_CONVERSION.has(suffix.value)
      )
      if (current !== generation) return
      data.value = bytes
      if (IMAGE_SUFFIXES.has(suffix.value)) {
        imageUrl.value = URL.createObjectURL(new Blob([bytes], { type: imageMimeOf(suffix.value) }))
      } else if (view.value !== 'pages' && view.value !== 'sheet' && bytes.byteLength <= 1024 * 1024) {
        const raw = new Uint8Array(bytes)
        if (!raw.slice(0, 8192).includes(0)) {
          try {
            text.value = new TextDecoder('utf-8', { fatal: true }).decode(raw)
          } catch {
            /* Binary files keep their download action. */
          }
        }
      }
    } catch (e) {
      if (current === generation)
        error.value = e instanceof Error ? e.message : t('tasks.artifactComparison.previewError')
    } finally {
      if (current === generation) loading.value = false
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  generation++
  clear()
})
</script>

<template>
  <section class="version-preview">
    <header class="pa-3">
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
      <PreviewPages v-if="view === 'pages'" :data="data" />
      <PreviewSheet v-else-if="view === 'sheet'" :data="data" :kind="suffix === 'csv' ? 'csv' : 'workbook'" />
      <img v-else-if="imageUrl" :src="imageUrl" :alt="version.filename || ''" />
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
