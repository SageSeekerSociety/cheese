<script setup lang="ts">
// 一份文件的字节，按它的类型摆出来：文档和 PDF 一页一页、表格一张一张、图片直接显
// 示、代码带行号和高亮、文本原样，其余的说一句只能下载。
//
// 字节从哪来由调用方给（`read`）：资料库的一份、交付的某一版，取法不一样，摆法一样。
// Office 文档浏览器画不了，`read(true)` 要的是服务端转好的 PDF。
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import DesignImage from '@/components/panels/preview/DesignImage.vue'
import PreviewCode from '@/components/panels/preview/PreviewCode.vue'
import PreviewMarkdown from '@/components/panels/preview/PreviewMarkdown.vue'
import PreviewPages from '@/components/panels/preview/PreviewPages.vue'
import PreviewSheet from '@/components/panels/preview/PreviewSheet.vue'
import PreviewSlides from '@/components/panels/preview/PreviewSlides.vue'
import { t } from '@/i18n'
import {
  codeLanguageOf,
  DOCUMENT_TYPES,
  headOf,
  IMAGE_SUFFIXES,
  imageMimeOf,
  NEEDS_CONVERSION,
  PREVIEW_TEXT_BYTES,
  sheetKindOf,
  SLIDE_SUFFIXES,
  suffixOf,
} from '@/lib/fileKind'

const props = withDefaults(
  defineProps<{
    filename: string
    /** 换了这个值就重新读：同一个名字被替换成新版本时也要换。 */
    source: string
    read: (asPdf: boolean) => Promise<ArrayBuffer>
    /** 字节的来处能不能把 Office 文档转成 PDF。不能（空间资料库）就不去读，直接说只能下载。 */
    convertible?: boolean
  }>(),
  // An absent boolean prop is cast to false, which would make every caller
  // that never mentions it unable to convert.
  { convertible: true }
)

const data = ref<ArrayBuffer | null>(null)
const imageUrl = ref('')
const text = ref<string | null>(null)
/** 摆的只是开头、底下的没摆（超过 `PREVIEW_TEXT_BYTES` 的文本）。 */
const cut = ref(false)
const loading = ref(false)
/** 失败的原因，服务器给了就照原样；`null` = 没失败、也没在失败。空串是
 *  「失败了但没有原因」，那也要摆出重试按钮，所以用 null 而不是 '' 当哨兵。 */
const error = ref<string | null>(null)
const suffix = computed(() => suffixOf(props.filename))
const view = computed(() => DOCUMENT_TYPES[suffix.value]?.view)
const downloadOnly = computed(() => !props.convertible && NEEDS_CONVERSION.has(suffix.value))
let generation = 0

function clear() {
  data.value = null
  text.value = null
  cut.value = false
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}

// 读一次。`watch` 在换文件时调它，失败后按重试也调它——两条路走同一段，
// 不会出现「换了文件但重试逻辑没跟上」这种分叉。慢网下一份预览要几十秒，
// 所以它在途中的样子（骨架）和失败后的样子（原因 + 重试）都在模板里先摆好。
async function load() {
  const current = ++generation
  clear()
  error.value = null
  if (downloadOnly.value) return
  loading.value = true
  try {
    const bytes = await props.read(NEEDS_CONVERSION.has(suffix.value))
    if (current !== generation) return
    data.value = bytes
    if (IMAGE_SUFFIXES.has(suffix.value)) {
      imageUrl.value = URL.createObjectURL(new Blob([bytes], { type: imageMimeOf(suffix.value) }))
    } else if (view.value !== 'pages' && view.value !== 'sheet') {
      const raw = new Uint8Array(bytes)
      // 文本按 `head` 的同一把尺子取（`textfile.PREVIEW_TEXT_BYTES`）：这个数以内
      // 整份摆，超过就摆开头。以前超过 1 MB 就一个字都不摆、只给下载，理由和预览
      // 面板那一支一样——读者要的是开头，不是「什么都没有」。
      const window = raw.slice(0, PREVIEW_TEXT_BYTES)
      const bytesCut = raw.byteLength > PREVIEW_TEXT_BYTES
      if (!window.slice(0, 8192).includes(0)) {
        try {
          // 截断时末尾会切在多字节字中间，解码器那时不严；退回最后一个换行保证
          // 一行一行是齐的（同后端 `head_text`）。
          const whole = new TextDecoder('utf-8', { fatal: !bytesCut }).decode(window)
          const kept = headOf(whole, bytesCut)
          text.value = kept.head
          cut.value = kept.cut
        } catch {
          /* 不是文本：只能下载。 */
        }
      }
      if (text.value === null) cut.value = false
    }
  } catch (e) {
    if (current === generation) error.value = e instanceof Error ? e.message : ''
  } finally {
    if (current === generation) loading.value = false
  }
}

watch(() => [props.filename, props.source], load, { immediate: true })

onBeforeUnmount(() => {
  generation++
  clear()
})
</script>

<template>
  <div class="file-preview">
    <!-- 内容在路上就先画出「一页内容」的形状：慢网下一份预览要几十秒，一行
         「正在读取」消失之后就是一片空白，看起来像坏了。骨架一直在，直到字节到
         货为止；LoadingSkeleton 自己带 role="status"。 -->
    <p v-if="downloadOnly" class="file-preview__note t-body c-muted">{{ t('work.library.downloadOnly') }}</p>
    <LoadingSkeleton v-else-if="loading" variant="doc" />
    <!-- 读失败就地换成原因加一条重试的路，而不是把用户留在一扇空窗前面。 -->
    <BaseLoadError v-else-if="error !== null" :title="t('work.library.previewError')" :error="error" @retry="load" />
    <template v-else-if="data">
      <PreviewSlides v-if="SLIDE_SUFFIXES.has(suffix)" :data="data" :title="filename" />
      <PreviewPages v-else-if="view === 'pages'" :data="data" />
      <PreviewSheet v-else-if="view === 'sheet'" :data="data" :kind="sheetKindOf(suffix)" />
      <!-- copy-code：同一份 md 在对话、任务预览、资料库三处渲染，代码块右上角那两颗
           （自动换行 / 复制）要一处不缺——少一处，读者就得记住「这个面板没有」。 -->
      <PreviewMarkdown v-else-if="view === 'markdown' && text !== null" :source="text" copy-code />
      <PreviewCode v-else-if="view === 'code' && text !== null" :source="text" :language="codeLanguageOf(suffix)" />
      <DesignImage v-else-if="imageUrl" :src="imageUrl" :alt="filename" :identity="source" />
      <pre v-else-if="text !== null" class="file-preview__text t-body" :class="{ 'file-preview__text--cut': cut }">{{
        text
      }}</pre>
      <p v-else class="file-preview__note t-body c-muted">{{ t('work.library.downloadOnly') }}</p>
    </template>
    <!-- 摆了开头、底下没摆：说清这一点（下载看整份），别让读者以为文件就这么长。 -->
    <p v-if="cut" class="file-preview__cut t-meta c-muted" role="status">
      {{ t('work.library.headOnly', { path: filename }) }}
    </p>
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
/* 只摆了开头：末尾压一条渐隐，读得出底下还有。 */
.file-preview__text--cut {
  mask-image: linear-gradient(to bottom, var(--ink) calc(100% - 24px), transparent);
}
.file-preview__cut {
  margin: 0;
  padding: 6px 16px 16px;
  border-top: 1px solid var(--line);
  text-align: center;
}
</style>
