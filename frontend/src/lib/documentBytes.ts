// 一份文档拿去给查看器画之前，先要有它的字节。
//
// 两条路：浏览器画不出来的（.docx / .pptx / …）先由平台转成 PDF，其余（PDF、表格、
// 图片）直接读原始字节。两个面板都要做这件事——预览看的是芝士交付的那一份，改动看
// 的是某个任务分支上的那一份——所以取字节这件事在这里一次写完，而不是各写一遍：
// 各写一遍的表现是同一份文档在两处显示得不一样。
//
// 「还是不是同一份文档」那个判断不住在这里，在 lib/documentIdentity.ts：它一次接口都
// 不调，而几个只做这个判断的钩子不该因为要它而够得着接口层。
import type { FileSource } from '../cx_types'
import type { DocumentIdentity, DocumentSnapshot } from './documentIdentity'

import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { previewDocumentPdfSnapshot, previewFileBytes, PreviewRendererUnavailable } from '../api'
import { t } from '../i18n'

import { NEEDS_CONVERSION, suffixOf } from './fileKind'

export interface DocumentSource {
  /** 房间。 */
  topicId: () => string | null
  /** 工作区相对路径。 */
  path: () => string | null
  /** 这个文件的版本，用来判断要不要重取。 */
  version: () => string | null
  /** 哪个来源：某个任务的工作树，还是房间自己的文件（null）。 */
  task?: () => string | null
  source?: () => FileSource
  /** 打一下就重取，版本没变也重取——修订处理完了就是这种情况。 */
  nonce?: () => number
  /** 这个文件要不要取字节。Markdown 不要：它的正文已经在文件内容里了，取一份
   *  PDF 只会把一篇好端端的 .md 变成「转换失败」。 */
  enabled?: () => boolean
}

export function useDocumentBytes(source: DocumentSource) {
  const snapshot = shallowRef<DocumentSnapshot | null>(null)
  const bytes = computed(() => snapshot.value?.bytes ?? null)
  const loading = ref(false)
  const error = ref('')
  /** 这个部署没有文档转换服务。和「这个文件转不了」是两件事：换个文件也一样。 */
  const rendererMissing = ref(false)
  let generation = 0
  let loadedKey = ''
  let disposed = false

  function capture() {
    const tid = source.topicId()
    const path = source.path()
    if (!tid || !path || (source.enabled && !source.enabled())) return null
    const identity: DocumentIdentity = {
      topicId: tid,
      path,
      taskId: source.task?.() ?? null,
      source: source.source?.() ?? 'live',
      version: source.version(),
    }
    const key = JSON.stringify([identity, source.nonce?.() ?? 0])
    return { identity, key }
  }

  async function load() {
    const captured = capture()
    if (!captured || disposed) return
    if (captured.key === loadedKey && snapshot.value) return
    const mine = ++generation
    const current = () => !disposed && mine === generation && capture()?.key === captured.key
    loading.value = true
    error.value = ''
    rendererMissing.value = false
    try {
      const { identity } = captured
      let got: { bytes: ArrayBuffer; sourceVersion: string | null }
      if (NEEDS_CONVERSION.has(suffixOf(identity.path))) {
        got = await previewDocumentPdfSnapshot(identity.topicId, identity.path, identity.taskId, identity.source)
      } else {
        const original = await previewFileBytes(identity.topicId, identity.path, identity.taskId, identity.source)
        if (!current()) return
        const digest = globalThis.crypto?.subtle ? await globalThis.crypto.subtle.digest('SHA-256', original) : null
        const sourceVersion = digest
          ? Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0'))
              .join('')
              .slice(0, 16)
          : null
        got = { bytes: original, sourceVersion }
      }
      if (!current()) return
      snapshot.value = { bytes: got.bytes, identity: captured.identity, sourceVersion: got.sourceVersion }
      loadedKey = captured.key
    } catch (e) {
      if (!current()) return
      // 保留已经在屏幕上的那一份。刷新失败时把它清掉，读者失去的是一份本来好好的
      // 文档，换来一句错误——而这份文档仍然是这个文件最新的可见状态。
      loadedKey = ''
      rendererMissing.value = e instanceof PreviewRendererUnavailable
      error.value = e instanceof Error ? e.message : t('files.preview.cannotShow')
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  function forget() {
    generation += 1
    snapshot.value = null
    loading.value = false
    loadedKey = ''
    error.value = ''
    rendererMissing.value = false
  }

  watch([source.topicId, source.path, source.task ?? (() => null), source.source ?? (() => 'live')], forget, {
    flush: 'sync',
  })

  watch(
    [
      source.topicId,
      source.path,
      source.version,
      source.task ?? (() => null),
      source.source ?? (() => 'live'),
      source.nonce ?? (() => 0),
      source.enabled ?? (() => true),
    ],
    () => {
      generation += 1
      loading.value = false
      if (source.enabled && !source.enabled()) {
        forget()
        return
      }
      void load()
    },
    { immediate: true, flush: 'sync' }
  )

  onBeforeUnmount(() => {
    disposed = true
    generation += 1
    loading.value = false
  })

  return { bytes, snapshot, loading, error, rendererMissing, load, forget }
}
