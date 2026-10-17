// 「改动」这一格把打开的那一份文件存回去：带版本存，以及芝士抢先改了同一份文件
// 之后的冲突与逐处取舍。
import type { Ref } from 'vue'
import type { MergeConflict } from '../../types/reviewComment'
import type { FileDraft } from '../useTopicMemory'
import type { PanelChangesProps } from './types'

import { ApiError, readFile, writeFile } from '../../api'

import { t } from '@/i18n'

export interface FileSaveDeps {
  props: PanelChangesProps
  /** 这一格看的那件任务。 */
  task: Ref<string | null>
  openPath: Ref<string | null>
  fileDraft: Ref<string>
  /** 上一次读回来或存下去的那一份内容，脏不脏看它。 */
  fileSaved: Ref<string>
  /** 读回来时的那一版：存的时候原样带回去。 */
  fileVersion: Ref<string | null>
  fileSaving: Ref<boolean>
  fileDirty: Ref<boolean>
  fileReadOnly: Ref<boolean>
  /** 后端把这次保存判成冲突（不是逐处合并的那一种）。 */
  fileConflict: Ref<boolean>
  /** 逐处取舍的那几段，等人在界面上选。 */
  fileMerge: Ref<MergeConflict | null>
  /** 没存下的草稿按「来源 + 路径」记（`useTopicMemory`）。 */
  drafts: Map<string, FileDraft>
  draftKey: (path: string) => string
  /** 这一格现在这一代；路上那一次回来时对不上就丢掉。 */
  epoch: () => number
  errorMsg: Ref<string | null>
  /** 重新读这一份文件（丢掉自己的改动，看最新的那一版）。 */
  reload: (path: string) => Promise<void>
}

/** 存回打开的那一份文件，以及它和芝士撞上时的两条出路。 */
export function usePanelFileSave(deps: FileSaveDeps) {
  const {
    props,
    task: selectedTask,
    openPath,
    fileDraft,
    fileSaved,
    fileVersion,
    fileSaving,
    fileDirty,
    fileReadOnly,
    fileConflict,
    fileMerge,
    drafts,
    draftKey,
    epoch,
    errorMsg,
    reload,
  } = deps

  // Every save carries the version on which the user's decision was based, and the
  // text it named: the backend merges the two edits against it when the file moved on.
  async function writeOpenFile(expected: string | null, base: string = fileSaved.value) {
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    const path = openPath.value
    if (!pid || !path || !selectedTask.value || fileReadOnly.value || !fileDirty.value || fileSaving.value) return
    const draft = fileDraft.value
    const key = draftKey(path)
    const epochAt = epoch()
    fileSaving.value = true
    errorMsg.value = null
    try {
      const res = await writeFile(pid, path, draft, tid ?? undefined, expected, task, base)
      if (drafts.get(key)?.content === draft) drafts.delete(key)
      // The answer is only about the file that was open in the topic that was
      // open — anything else finished after a switch and must be dropped.
      if (selectedTask.value !== task || openPath.value !== path || epoch() !== epochAt) return
      // 合并过的话，存下去的是两边改动合起来的那一份：编辑器里换成它。
      const saved = res.merged && typeof res.content === 'string' ? res.content : draft
      fileSaved.value = saved
      if (saved !== draft) fileDraft.value = saved
      fileVersion.value = res.version
      fileConflict.value = false
      fileMerge.value = null
    } catch (e) {
      if (selectedTask.value !== task || openPath.value !== path || epoch() !== epochAt) return
      const merge = e instanceof ApiError ? (e.data as MergeConflict | undefined) : undefined
      if (e instanceof ApiError && e.status === 409 && merge?.regions) {
        // Both edited the same lines: pick a version for each, then save over theirs.
        fileMerge.value = merge
      } else if (e instanceof ApiError && e.status === 409) {
        // 芝士 wrote this file since it was read. Neither side wins by default:
        // show the conflict and let the human reload or overwrite on purpose.
        fileConflict.value = true
      } else {
        errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.saveFailed')
      }
    } finally {
      if (selectedTask.value === task && epoch() === epochAt) fileSaving.value = false
    }
  }

  function saveFile() {
    void writeOpenFile(fileVersion.value)
  }

  // 逐处选完：存的是选出来的那一份，基于芝士那一版。
  function resolveMerge(content: string) {
    const merge = fileMerge.value
    if (!merge) return
    fileDraft.value = content
    void writeOpenFile(merge.version, merge.base)
  }
  function cancelMerge() {
    fileMerge.value = null
  }

  // 冲突后的两条出路,都由人点：丢掉自己的改动看最新的，或者明知有冲突仍然覆盖。
  async function overwriteFile() {
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    const path = openPath.value
    const epochAt = epoch()
    if (!pid || !path || fileReadOnly.value) return
    try {
      const current = await readFile(pid, path, tid, task, 'live')
      if (epochAt !== epoch() || openPath.value !== path) return
      await writeOpenFile(current.version)
    } catch (error) {
      if (epochAt === epoch())
        errorMsg.value = error instanceof Error ? error.message : t('work.room.changes.readFileFailed')
    }
  }

  function reloadOpenFile() {
    const path = openPath.value
    if (path) {
      drafts.delete(draftKey(path))
      fileDraft.value = fileSaved.value
      void reload(path)
    }
  }

  return { fileMerge, writeOpenFile, saveFile, resolveMerge, cancelMerge, overwriteFile, reloadOpenFile }
}
