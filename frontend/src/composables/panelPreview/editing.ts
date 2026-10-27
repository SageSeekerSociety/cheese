// 「预览」这一格的在线编辑、历史，和这一份 .docx 的修订。
//
// 这三件事原先长在展示组件里（编辑器那一包在自己身上，历史那一包在历史栏身上，修订那一包在
// 清单身上），于是「预览」这一格顺着一层层 import 够得着接口层。现在三份取数都在这儿，画的
// 那几层只认递下去的这几包。
//
// `editing` 是「现在开着哪一份的编辑会话」：它同时决定了编辑器开在哪一份上、旁边那一栏历史
// 读的是哪一份（编辑时读编辑器里那份，否则读预览台上那份），以及两处恢复/处理完之后重画谁。
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { ref } from 'vue'

import { isLibraryPath } from '../../lib/library'
import { useDocumentRevisions } from '../useDocumentRevisions'
import { useRoomFileEditor } from '../useRoomFileEditor'
import { useRoomFileHistory } from '../useRoomFileHistory'

export interface EditingDeps {
  props: PanelPreviewProps
  /** 这一格摆着的那一份：编辑会话和修订都长在它上面。 */
  state: PreviewState
  /** 编辑里「另存一份」另存出了一份新的：宿主要把它开成自由区的一个页签。 */
  onOpenFile?: (path: string) => void
  /** 文件变了、版本却没变（是这里改的）：说一声让那一页的字节重取。 */
  refreshDocument: () => void
}

/** 这一格的编辑器、那一栏历史和这一份 .docx 的修订。 */
export function usePanelPreviewEditing(deps: EditingDeps) {
  const { props, state, onOpenFile, refreshDocument } = deps

  const editing = ref<string | null>(null)
  const showHistory = ref(false)

  function openEditor(path: string) {
    editing.value = path
    onOpenFile?.(path)
  }

  function closeEditor() {
    editing.value = null
    // 编辑器里存下的那一版就是这份文件现在的样子：字节按版本缓存，版本没变，所以自己要
    // 说一句让它重取。
    refreshDocument()
  }

  function toggleHistory() {
    showHistory.value = !showHistory.value
  }

  // 这一份 .docx 的修订。只在 .docx 上读：别的类型没有修订，读也是白读一次。
  const revs = useDocumentRevisions(
    {
      topicId: () => props.topicId,
      path: () => (state.documentSuffix.value === 'docx' ? state.previewFile.value?.path ?? null : null),
      version: () => state.previewFile.value?.version ?? null,
      task: () => null,
      source: () => state.previewFile.value?.source ?? 'live',
    },
    { onDecided: () => refreshDocument() }
  )

  const editor = useRoomFileEditor(
    { topicId: () => props.topicId, path: () => editing.value },
    { onOpened: (path) => openEditor(path) }
  )

  // 编辑器和预览台上那一份共用同一栏历史：编辑时它读编辑器里那份（刚存下的那一版也在里面），
  // 否则读预览台上这份。恢复完，编辑中还开着的会话要重新载入新版本，否则把那一页重取一遍。
  //
  // 房间里的文件一打开就读：文档条上那一行「第 N 版 · 谁 · 什么时候」和「本版共修改 N 处」
  // 都从这份清单来（`./versions.ts`），一份文件多问一次清单，换来读者不用点开历史就知道
  // 这是哪一版。资料库里的原件没有历史，不问；编辑器开着时读编辑器里那份。
  const fileHistory = useRoomFileHistory(
    {
      topicId: () => props.topicId,
      path: () => editing.value ?? state.previewFile.value?.path ?? null,
      // 编辑器开着时这一栏读的是编辑器里那份：它自己存下的那一版由 `editor.savedSeq` 报回来，
      // 变了就让这一栏重读一遍，刚存下的那版才当场出现在列表里。
      version: () =>
        editing.value
          ? editor.savedSeq.value === null
            ? null
            : String(editor.savedSeq.value)
          : state.previewFile.value?.version ?? null,
      enabled: () => {
        const path = state.previewFile.value?.path
        return !!editing.value || showHistory.value || (!!path && !isLibraryPath(path))
      },
    },
    {
      onRestored: () => {
        if (editing.value) void editor.start()
        else refreshDocument()
      },
    }
  )

  return { revs, editing, showHistory, editor, fileHistory, openEditor, closeEditor, toggleHistory }
}
