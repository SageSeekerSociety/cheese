// 交上来的一份文本文件（一份 Markdown 报告）的正文：从它的存储地址读。交上来的文件只有那一个
// 地址，批改的人打开文件走的也是它（`_submitted_file_to_api`）。
import type { MaybeRefOrGetter } from 'vue'

import { ref, toValue, watch } from 'vue'

import { getFullAttachmentUrl } from '@/utils/materials'

/** `url` 换了就重读；`skip` 为真时不读（太大、不该预览）。`failed` 是读过但没读到。 */
export function useSubmittedText(url: MaybeRefOrGetter<string>, skip: MaybeRefOrGetter<boolean> = false) {
  const text = ref<string | null>(null)
  const failed = ref(false)
  let generation = 0
  watch(
    () => [toValue(url), toValue(skip)] as const,
    async ([address, skipped]) => {
      const current = ++generation
      text.value = null
      failed.value = false
      if (skipped) return
      try {
        const response = await fetch(getFullAttachmentUrl(address))
        if (!response.ok) throw new Error(String(response.status))
        const body = await response.text()
        if (current === generation) text.value = body
      } catch {
        if (current === generation) failed.value = true
      }
    },
    { immediate: true }
  )
  return { text, failed }
}
