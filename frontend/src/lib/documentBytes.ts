// 一份文档拿去给查看器画之前，先要有它的字节。
//
// 三条路：浏览器画不出来的（.docx / .pptx / …）先由平台转成 PDF；表格里连原始字节
// 都读不出单元格的那一种（.xls）转成 xlsx；其余（PDF、.xlsx、图片、csv、md）直接读
// 原始字节。哪一条由 `previewSource` 说了算。两个面板都要做这件事——预览看的是芝士
// 交付的那一份，改动看的是某个任务分支上的那一份——所以取字节这件事在这里一次写完，
// 而不是各写一遍：各写一遍的表现是同一份文档在两处显示得不一样。
import { ref, watch } from 'vue'

import { previewDocumentPdf, previewDocumentXlsx, previewFileBytes, PreviewRendererUnavailable } from '../api'

import { previewSource } from './fileKind'

export interface DocumentSource {
  /** 房间。 */
  topicId: () => string | null
  /** 工作区相对路径。 */
  path: () => string | null
  /** 这个文件的版本，用来判断要不要重取。 */
  version: () => string | null
  /** 哪个来源：某个任务的工作树，还是房间自己的文件（null）。 */
  task?: () => string | null
  /** 打一下就重取，版本没变也重取——修订处理完了就是这种情况。 */
  nonce?: () => number
  /** 这个文件要不要取字节。Markdown 不要：它的正文已经在文件内容里了，取一份
   *  PDF 只会把一篇好端端的 .md 变成「转换失败」。 */
  enabled?: () => boolean
}

export function useDocumentBytes(source: DocumentSource) {
  const bytes = ref<ArrayBuffer | null>(null)
  const loading = ref(false)
  const error = ref('')
  /** 这个部署没有文档转换服务。和「这个文件转不了」是两件事：换个文件也一样。 */
  const rendererMissing = ref(false)
  let generation = 0
  let loadedKey = ''

  async function load() {
    const tid = source.topicId()
    const path = source.path()
    const task = source.task?.() ?? null
    if (!tid || !path || (source.enabled && !source.enabled())) return
    const key = `${tid}:${task ?? ''}:${path}:${source.version() ?? ''}:${source.nonce?.() ?? 0}`
    if (key === loadedKey && bytes.value) return
    const mine = ++generation
    loading.value = true
    error.value = ''
    rendererMissing.value = false
    try {
      const from = previewSource(path)
      const got =
        from === 'pdf'
          ? await previewDocumentPdf(tid, path, task)
          : from === 'xlsx'
            ? await previewDocumentXlsx(tid, path, task)
            : await previewFileBytes(tid, path, task)
      if (mine !== generation) return
      bytes.value = got
      loadedKey = key
    } catch (e) {
      if (mine !== generation) return
      // 保留已经在屏幕上的那一份。刷新失败时把它清掉，读者失去的是一份本来好好的
      // 文档，换来一句错误——而这份文档仍然是这个文件最新的可见状态。
      loadedKey = ''
      rendererMissing.value = e instanceof PreviewRendererUnavailable
      error.value = e instanceof Error ? e.message : '无法显示这个文件'
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  function forget() {
    generation += 1
    bytes.value = null
    loadedKey = ''
    error.value = ''
  }

  watch(
    [
      source.path,
      source.version,
      source.task ?? (() => null),
      source.nonce ?? (() => 0),
      source.enabled ?? (() => true),
    ],
    () => {
      if (source.enabled && !source.enabled()) {
        forget()
        return
      }
      void load()
    },
    { immediate: true }
  )

  return { bytes, loading, error, rendererMissing, load, forget }
}
