<script setup lang="ts">
// 清单上某一版交出去的那一份，画在屏幕上。
//
// 取的是**当时交出去的那个快照**，不是现在从源重建一次的结果：半年之后依赖变了、
// 字体没了，重建出来的可能和当时交出去的不是同一个东西，而人要看他交出去的那一份。
//
// 失败要说得出是哪一种。这个部署没接转换服务，换一份文件也一样；这份文件转不了，
// 换一份就好了——两句话不同，能不能重试也不同，所以两句话都要留着自己的形状，还得
// 留着下载这条退路：预览没有，不等于这一版交出去的东西没有了。
import type { ArtifactVersion } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { artifactVersionBytes, artifactVersionFileUrl, downloadFile, PreviewRendererUnavailable } from '../api'
import { t } from '../i18n'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, suffixOf } from '../lib/fileKind'

import PreviewPages from './panels/preview/PreviewPages.vue'
import PreviewSheet from './panels/preview/PreviewSheet.vue'

const props = defineProps<{ projectId: string; artifactId: string; version: ArtifactVersion }>()
const data = ref<ArrayBuffer | null>(null)
const imageUrl = ref('')
const text = ref<string | null>(null)
const loading = ref(false)
const error = ref('')
/** 这个部署没有文档转换服务。和「这份文件转不了」是两件事。 */
const rendererMissing = ref(false)
const downloading = ref(false)
const suffix = computed(() => suffixOf(props.version.filename ?? ''))
const view = computed(() => DOCUMENT_TYPES[suffix.value]?.view)
const isFile = computed(() => props.version.kind === 'file' && !!props.version.filename)
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
    rendererMissing.value = false
    loading.value = false
    if (!isFile.value) return
    loading.value = true
    try {
      // 不带参数地说「要预览的那份形态」：转换表只有后端有，这一侧不再猜哪个后缀
      // 要转。旧表格 (.xls) 会因此拿到一份新的 .xlsx，而不是一路撞进读不懂 BIFF
      // 的阅读器里。
      const bytes = await artifactVersionBytes(props.projectId, props.artifactId, props.version.card_id)
      if (current !== generation) return
      data.value = bytes
      if (IMAGE_SUFFIXES.has(suffix.value)) {
        imageUrl.value = URL.createObjectURL(
          new Blob([bytes], { type: suffix.value === 'png' ? 'image/png' : 'image/jpeg' })
        )
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
      if (current !== generation) return
      rendererMissing.value = e instanceof PreviewRendererUnavailable
      error.value = e instanceof Error ? e.message : t('tasks.artifactVersion.loadError')
    } finally {
      if (current === generation) loading.value = false
    }
  },
  { immediate: true }
)

async function download() {
  const filename = props.version.filename
  if (!filename) return
  downloading.value = true
  error.value = ''
  try {
    await downloadFile(artifactVersionFileUrl(props.projectId, props.artifactId, props.version.card_id), filename)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.artifactVersion.downloadError')
  } finally {
    downloading.value = false
  }
}

onBeforeUnmount(() => {
  generation++
  clear()
})
</script>

<template>
  <section class="version-preview">
    <header class="version-preview__head pa-3">
      <div class="version-preview__id">
        <h3 class="t-title">{{ t('tasks.artifactVersion.number', { number: version.number }) }}</h3>
        <p class="t-body c-muted version-preview__name">{{ version.filename || version.subject }}</p>
      </div>
      <!-- 预览看不了是常事（没接服务、太大、格式不认识），而这一版交出去的字节一直
           在。所以下载永远在这一格，不藏在版本列表里。 -->
      <v-btn
        v-if="isFile"
        size="small"
        variant="text"
        color="on-surface-variant"
        :loading="downloading"
        @click="download"
      >
        {{ t('tasks.artifactVersion.download') }}
      </v-btn>
    </header>

    <div v-if="loading" class="version-preview__state" role="status">
      <v-progress-circular indeterminate color="primary" size="24" />
      <p class="t-meta c-faint mt-3">{{ t('tasks.artifactVersion.loading') }}</p>
    </div>

    <!-- 两种失败说的不是一回事：一种是这个部署缺服务（换一份文件也一样），一种是
         这份文件转不了（别的文件仍然看得见）。这一句和房间那面板是同一句。 -->
    <div v-else-if="rendererMissing" class="version-preview__state" role="status">
      <v-icon size="28" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
      <p class="t-body">{{ t('tasks.artifactVersion.rendererMissing') }}</p>
    </div>

    <div v-else-if="error" class="version-preview__state" role="alert">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <p class="t-body c-danger">{{ error }}</p>
    </div>

    <a
      v-else-if="version.kind === 'link' && version.url"
      class="version-preview__link pa-4 t-body"
      :href="version.url"
      target="_blank"
      rel="noopener noreferrer"
      >{{ version.url }}</a
    >

    <div v-else-if="version.kind === 'merge'" class="version-preview__state">
      <v-icon size="28" class="text-disabled mb-2">mdi-source-merge</v-icon>
      <p class="t-body">{{ t('tasks.artifactVersion.merge') }}</p>
    </div>

    <div v-else-if="data" class="version-preview__body">
      <PreviewPages v-if="view === 'pages'" :data="data" />
      <PreviewSheet v-else-if="view === 'sheet'" :data="data" :kind="suffix === 'csv' ? 'csv' : 'workbook'" />
      <img v-else-if="imageUrl" :src="imageUrl" :alt="version.filename || ''" />
      <pre v-else-if="text !== null" class="pa-3 t-body">{{ text }}</pre>
      <p v-else class="pa-4 t-body c-muted">{{ t('tasks.artifactVersion.downloadOnly') }}</p>
    </div>

    <p v-else class="version-preview__state t-body c-muted">{{ t('tasks.artifactVersion.noPreview') }}</p>
  </section>
</template>

<style scoped>
.version-preview {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.version-preview__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
}
.version-preview__id {
  min-width: 0;
}
.version-preview__name {
  overflow-wrap: anywhere;
}
.version-preview__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 40px 24px;
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
.version-preview__link {
  display: block;
  overflow-wrap: anywhere;
  color: var(--text);
}
</style>
