// 一版产物此刻该画成什么：它那几个字节，以及由它们长出来的那张图 / 那段文本。
//
// 拆自 `components/ArtifactVersionPreview.vue`：取数在这一层，组件只管画——组件不
// 直接碰 API 层（`.claude/rules/architecture.md` 的组件边界）。
//
// 字节是随版本变的（换一版就重取），所以这里连「换到哪一版了」的记账一起收着：老的
// 那一次回来得晚就丢掉（`generation`），而且每换一次先把上一版的东西清掉——否则并排
// 比较两版时，新的一版会短暂地画着上一版的图。
import type { MaybeRefOrGetter } from 'vue'
import type { ArtifactVersion } from '@/api'

import { computed, onBeforeUnmount, ref, toValue, watch } from 'vue'

import { artifactVersionBytes } from '@/api'
import { t } from '@/i18n'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, imageMimeOf, NEEDS_CONVERSION, suffixOf } from '@/lib/fileKind'

export interface ArtifactVersionSource {
  projectId: string
  artifactId: string
  version: ArtifactVersion
}

/** 这一版产物的字节，和由它们画出来的那几样。`view` 说该用哪一个画法。 */
export function useArtifactVersionPreview(source: MaybeRefOrGetter<ArtifactVersionSource>) {
  const data = ref<ArrayBuffer | null>(null)
  const imageUrl = ref('')
  const text = ref<string | null>(null)
  const loading = ref(false)
  const error = ref('')
  const suffix = computed(() => suffixOf(toValue(source).version.filename ?? ''))
  const view = computed(() => DOCUMENT_TYPES[suffix.value]?.view)
  let generation = 0

  function clear() {
    data.value = null
    text.value = null
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = ''
  }

  watch(
    () => {
      const s = toValue(source)
      return [s.projectId, s.artifactId, s.version.card_id]
    },
    async () => {
      const current = ++generation
      clear()
      error.value = ''
      loading.value = false
      const s = toValue(source)
      if (s.version.kind !== 'file') return
      loading.value = true
      try {
        const bytes = await artifactVersionBytes(
          s.projectId,
          s.artifactId,
          s.version.card_id,
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

  return { data, imageUrl, text, loading, error, suffix, view }
}
