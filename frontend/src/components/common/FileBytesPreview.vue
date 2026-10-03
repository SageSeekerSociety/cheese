<script setup lang="ts">
// 一份文件的字节，按它的类型摆出来：文档和 PDF 一页一页、表格一张一张、图片直接显
// 示、文本原样，其余的说一句只能下载。
//
// 字节从哪来由调用方给（`read`）：资料库的一份、交付的某一版，取法不一样，摆法一样。
// Office 文档浏览器画不了，`read(true)` 要的是服务端转好的 PDF。
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import DesignImage from '@/components/panels/preview/DesignImage.vue'
import PreviewPages from '@/components/panels/preview/PreviewPages.vue'
import PreviewSheet from '@/components/panels/preview/PreviewSheet.vue'
import PreviewSlides from '@/components/panels/preview/PreviewSlides.vue'
import { t } from '@/i18n'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, imageMimeOf, NEEDS_CONVERSION, sheetKindOf, suffixOf } from '@/lib/fileKind'

const props = defineProps<{
  filename: string
  /** 换了这个值就重新读：同一个名字被替换成新版本时也要换。 */
  source: string
  read: (asPdf: boolean) => Promise<ArrayBuffer>
}>()

const data = ref<ArrayBuffer | null>(null)
const imageUrl = ref('')
const text = ref<string | null>(null)
const loading = ref(false)
const error = ref('')
const suffix = computed(() => suffixOf(props.filename))
const view = computed(() => DOCUMENT_TYPES[suffix.value]?.view)
let generation = 0

function clear() {
  data.value = null
  text.value = null
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}

watch(
  () => [props.filename, props.source],
  async () => {
    const current = ++generation
    clear()
    error.value = ''
    loading.value = true
    try {
      const bytes = await props.read(NEEDS_CONVERSION.has(suffix.value))
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
            /* 不是文本：只能下载。 */
          }
        }
      }
    } catch (e) {
      if (current === generation) error.value = e instanceof Error ? e.message : t('work.library.previewError')
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
  <div class="file-preview">
    <p v-if="loading" class="file-preview__note t-body c-muted" role="status">{{ t('work.library.previewLoading') }}</p>
    <p v-else-if="error" class="file-preview__note t-body c-danger" role="alert">{{ error }}</p>
    <template v-else-if="data">
      <PreviewSlides v-if="['pptx', 'ppt', 'odp'].includes(suffix)" :data="data" :title="filename" />
      <PreviewPages v-else-if="view === 'pages'" :data="data" />
      <PreviewSheet v-else-if="view === 'sheet'" :data="data" :kind="sheetKindOf(suffix)" />
      <DesignImage v-else-if="imageUrl" :src="imageUrl" :alt="filename" :identity="source" />
      <pre v-else-if="text !== null" class="file-preview__text t-body">{{ text }}</pre>
      <p v-else class="file-preview__note t-body c-muted">{{ t('work.library.downloadOnly') }}</p>
    </template>
  </div>
</template>

<style scoped>
.file-preview {
  height: 100%;
  min-height: 0;
  overflow: auto;
}
.file-preview__note {
  margin: 0;
  padding: 24px;
  text-align: center;
}
.file-preview__image {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0 auto;
}
.file-preview__text {
  margin: 0;
  padding: 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
