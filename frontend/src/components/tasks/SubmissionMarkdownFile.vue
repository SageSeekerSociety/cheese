<script setup lang="ts">
// 交上来的一份 Markdown 报告，按文档画出来。报告里图片的相对引用（`![](autograder.png)`）
// 指向同一项一起交的那几份文件：老师在这里直接看到截图，不用把每张图下载下来对着读。
//
// 字节从文件的存储地址读：交上来的文件只有那一个地址，批改的人打开文件走的也是它
// （`_submitted_file_to_api`）。
import type { FileMeta, Material } from '@/types'

import { computed, ref, watch } from 'vue'

import { getFullAttachmentUrl } from '@/utils/materials'

import MarkdownView from '@/components/common/MarkdownView.vue'
import { t } from '@/i18n'
import { PREVIEW_TEXT_BYTES } from '@/lib/fileKind'
import { siblingResolver } from '@/lib/submissionFiles'

const props = defineProps<{
  file: Material
  /** 同一项交的全部文件，含这一份。 */
  siblings: readonly Material[]
}>()

const source = ref<string | null>(null)
const failed = ref(false)
/** 超过预览上限的不读：卡片上的链接照样能打开整份。 */
const tooLarge = computed(() => (props.file.meta.size ?? 0) > PREVIEW_TEXT_BYTES)

const resolveImage = computed(() =>
  siblingResolver(
    props.siblings.map((sibling) => ({
      name: (sibling.meta as FileMeta).name,
      url: getFullAttachmentUrl(sibling.url),
    }))
  )
)

let generation = 0
watch(
  () => props.file.url,
  async (url) => {
    const current = ++generation
    source.value = null
    failed.value = false
    if (tooLarge.value) return
    try {
      const response = await fetch(getFullAttachmentUrl(url))
      if (!response.ok) throw new Error(String(response.status))
      const text = await response.text()
      if (current === generation) source.value = text
    } catch {
      if (current === generation) failed.value = true
    }
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="source !== null" class="submission-md">
    <MarkdownView :source="source" :resolve-image="resolveImage" />
  </div>
  <p v-else-if="failed" class="t-body c-muted">{{ t('tasks.submissionHistory.previewFailed') }}</p>
</template>

<style scoped>
.submission-md {
  max-height: 70vh;
  overflow: auto;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.submission-md :deep(img) {
  max-width: 100%;
}
</style>
