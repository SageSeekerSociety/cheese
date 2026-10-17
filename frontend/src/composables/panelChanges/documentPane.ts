// 「改动」这一格里打开的那一份文档：这一版画出来，外加它自己带的修订。
//
// 一份 .docx 的差异是一句「二进制文件不同」——按文件类型分派渲染器之前，这一格对一
// 份交付的文档能说的只有这句话。现在它画出这一版的页面，再把文件里的修订逐条列出
// 来：那才是「这一版比上一版改了什么」在一份 Word 文档里的真实形态。
import type { Ref } from 'vue'
import type { FileSource } from '../../cx_types'

import { computed, ref } from 'vue'

import { useDocumentBytes } from '../../lib/documentBytes'
import { DOCUMENT_TYPES, needsDocumentView, suffixOf } from '../../lib/fileKind'
import { useDocumentRevisions } from '../useDocumentRevisions'

export interface DocumentPaneDeps {
  /** 这一格看的话题：字节和修订都按它取。 */
  topicId: () => string | null
  /** 打开的那一份文件。 */
  path: Ref<string | null>
  /** 读回来时的那一版：字节和修订都按版本缓存。 */
  version: Ref<string | null>
  task: Ref<string | null>
  source: Ref<FileSource>
  /** 待审阅时修订只看不改：接受或拒绝会改动审的这一版。哪一处不要，写一条批注。 */
  readOnly: () => boolean
  /** 处理完一处修订，文件就变了：说一声，让调用者把树上的数字和 diff 重读。 */
  onChanged: (path: string | null) => Promise<void>
}

/** 打开的那一份文档的字节与修订。 */
export function usePanelDocument(deps: DocumentPaneDeps) {
  const {
    topicId,
    path: openPath,
    version: fileVersion,
    task: selectedTask,
    source: fileSource,
    readOnly,
    onChanged,
  } = deps

  const openIsDocument = computed(() => !!openPath.value && needsDocumentView(openPath.value))
  const openDocumentType = computed(() => (openPath.value ? DOCUMENT_TYPES[suffixOf(openPath.value)] ?? null : null))
  /** 修订只长在 .docx 上：其余文档（一张 PDF、一页幻灯片）没有「修订」这回事。 */
  const revisionPath = computed(() => (openPath.value && suffixOf(openPath.value) === 'docx' ? openPath.value : null))
  // 处理完一处修订，文件就变了，而字节是按版本缓存的——这里打一下让它重取。
  const docNonce = ref(0)
  const {
    bytes: docBytes,
    loading: docLoading,
    error: docError,
    rendererMissing: docRendererMissing,
  } = useDocumentBytes({
    topicId,
    path: () => openPath.value,
    version: () => fileVersion.value,
    task: () => selectedTask.value,
    source: () => fileSource.value,
    nonce: () => docNonce.value,
    enabled: () => openIsDocument.value,
  })

  async function onRevisionDecided() {
    const path = openPath.value
    docNonce.value += 1
    // 文件的版本变了，树上那几个数字也跟着变：两边都重读，别让读者对着旧数字看。
    await onChanged(path)
  }

  // 这一份 .docx 的修订：清单、只读怎么判、处理完干什么，都在那一份里。处理完这条活
  // 的文件就变了（`onRevisionDecided`）—— 这一格除了那一处，还要把树上的数字重读。
  const revs = useDocumentRevisions(
    {
      topicId,
      path: () => revisionPath.value,
      version: () => fileVersion.value,
      task: () => selectedTask.value,
      source: () => fileSource.value,
      readOnly,
    },
    { onDecided: () => void onRevisionDecided() }
  )

  return {
    openIsDocument,
    openDocumentType,
    revisionPath,
    docBytes,
    docLoading,
    docError,
    docRendererMissing,
    revs,
    onRevisionDecided,
  }
}
