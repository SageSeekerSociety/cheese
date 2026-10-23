// 「改动」这一格打开着的那一份文件：状态、清单、读取、草稿和只读判据。
//
// 这一层管的全是「对打开的那一份文件做什么」。工作树差异在 `gitChanges.ts`，文件树
// 在 `fileTree.ts`，存回去与冲突取舍在 `fileSave.ts`，文档的字节与修订在
// `documentPane.ts`，和上一版的比较在 `officeCompare.ts`；任务、来源和加载策略还在
// 组合根 `../usePanelChanges.ts`。
import type { ComputedRef, Ref } from 'vue'
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'
import type { MergeConflict } from '../../types/reviewComment'
import type { PanelChangesProps } from './types'

import { computed, onBeforeUnmount, ref } from 'vue'

import { downloadFile, listFiles, readFile, RequestTimeoutError, workspaceFileRawUrl } from '../../api'
import { parseDiffLines } from '../../lib/diff'
import { useRawImage } from '../useRawImage'
import { useTopicMemory } from '../useTopicMemory'

import { usePanelFileSave } from './fileSave'
import { usePanelFileTree } from './fileTree'

import { t } from '@/i18n'

/** 打开的文件看哪一面：它的 diff，还是可编辑的全文。 */
export type FileView = 'diff' | 'edit'

export interface FilePaneDeps {
  props: PanelChangesProps
  /** 这一格看的那件任务。 */
  task: Ref<string | null>
  /** 它的那一行（任务读回来之后才有）。 */
  currentTask: Ref<RoomTask | undefined>
  /** 打开的文件看哪一面：交上来的那一版，还是现场的工作区。 */
  source: Ref<FileSource>
  /** 这一支改到的文件，已经按每个文件一段切好。 */
  fileDiffs: ComputedRef<FileDiff[]>
  /** 上面那份清单按路径索引。 */
  diffByPath: ComputedRef<Map<string, FileDiff>>
  loading: Ref<boolean>
  sourceUnavailable: Ref<boolean>
  noRepo: Ref<boolean>
  noWorkYet: Ref<boolean>
  errorMsg: Ref<string | null>
  /** 这一格现在这一代；路上那一次回来时对不上就丢掉。 */
  epoch: () => number
  /** 开始一次前台读取：转圈要等最后一个回来的。 */
  startLoad: () => () => void
}

/** 打开的那一份文件的全部状态与动作。 */
export function usePanelFilePane(deps: FilePaneDeps) {
  const {
    props,
    task: selectedTask,
    currentTask,
    source: fileSource,
    fileDiffs,
    diffByPath,
    loading,
    sourceUnavailable,
    noRepo,
    noWorkYet,
    errorMsg,
    epoch,
    startLoad,
  } = deps

  // ---- 文件: a two-pane browser — the tree stays visible on the left, the opened
  // file loads on the right: its diff, or the editable text (save = 人改文件
  // 即指令). ----
  const files = ref<WorkspaceFile[]>([])
  const openPath = ref<string | null>(null)
  const fileDraft = ref<string>('')
  const fileSaved = ref<string>('') // last loaded/saved content, for the dirty flag
  const fileSaving = ref(false)
  const fileView = ref<FileView>('diff')
  // 每一次「打开哪一份」的序号：路上的读回来时对不上就丢掉。
  let fileRequest = 0
  // 横条上关于「这一份文件」的那半（路径、差异/编辑、保存）只在文件区真的摆出来时才有。
  const fileToolReady = computed(() => !sourceUnavailable.value && !noRepo.value && !loading.value && !errorMsg.value)
  const fileDirty = computed(() => fileDraft.value !== fileSaved.value)
  // Version of the open file as it was read; echoed back on save so a write that
  // lost a race to 芝士 is rejected instead of silently erasing their edits.
  const fileVersion = ref<string | null>(null)
  // Files that must not be edited as text: binary (a text round-trip destroys
  // them) or too large to send. They open read-only, with no 保存 button.
  const fileBinary = ref(false)
  const fileTooLarge = ref(false)
  const fileBytes = ref(0)
  const fileEditable = ref(false)
  const fileReadOnly = computed(
    () =>
      props.readOnly ||
      fileSource.value === 'committed' ||
      !fileEditable.value ||
      currentTask.value?.status !== 'open' ||
      // 待审阅时不直接改：审的这一版会在审的过程中变掉。想改的地方写成修改建议。
      currentTask.value?.card?.status === 'pending' ||
      fileBinary.value ||
      fileTooLarge.value ||
      openIsImage.value
  )
  // Drafts belong to a source and path, including the version they were edited from.
  // They outlive this panel (it is rebuilt for every topic): an unsaved edit left in
  // one topic's file is still there when the reader comes back to it.
  const { drafts, lastFiles } = useTopicMemory()
  function sourceKey() {
    return `${selectedTask.value}:${fileSource.value}`
  }
  function draftKey(path: string) {
    return `${sourceKey()}:${path}`
  }
  function keepDraft() {
    if (!openPath.value) return
    lastFiles.set(sourceKey(), openPath.value)
    if (fileDirty.value) {
      drafts.set(draftKey(openPath.value), {
        content: fileDraft.value,
        saved: fileSaved.value,
        version: fileVersion.value,
      })
    } else {
      drafts.delete(draftKey(openPath.value))
    }
  }
  function warnBeforeUnload(event: BeforeUnloadEvent) {
    if (!fileDirty.value && !drafts.size) return
    event.preventDefault()
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', warnBeforeUnload)
  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', warnBeforeUnload)
    keepDraft()
  })

  // Set when the backend rejected a save as a conflict. Nobody wins by default —
  // the human sees it and picks.
  const fileConflict = ref(false)
  // 存的时候和芝士这段时间的修改重叠了：三方的那几段，逐处选一个版本再存。
  const fileMerge = ref<MergeConflict | null>(null)

  const tree = usePanelFileTree({ fileDiffs, files })

  /** The open file's own diff, or null when this topic did not touch it. */
  const openDiff = computed<FileDiff | null>(() =>
    openPath.value ? diffByPath.value.get(openPath.value) ?? null : null
  )
  const openDiffLines = computed(() => (openDiff.value ? parseDiffLines(openDiff.value.body) : []))
  // 差异 is the default face of a changed file — reviewing is what this tab is
  // for — but a file with no diff has only one face, so the toggle is not offered.
  const effectiveView = computed<FileView>(() => (openDiff.value ? fileView.value : 'edit'))

  const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'])
  function isImagePath(path: string): boolean {
    return IMAGE_EXT.has(path.split('.').pop()?.toLowerCase() ?? '')
  }
  const openIsImage = computed(() => !!openPath.value && isImagePath(openPath.value))

  // Raw bytes of the open file: the image below is fetched from it, and the
  // download button hands it over for anything else that can't be shown as text.
  const openRawUrl = computed(() =>
    openPath.value && props.projectId
      ? workspaceFileRawUrl(
          props.projectId,
          openPath.value,
          props.topicId ?? undefined,
          selectedTask.value,
          fileSource.value
        )
      : ''
  )
  // <img> cannot carry Authorization, and /file/raw answers an anonymous read with
  // 404: the image is fetched with the header and shown from an object URL.
  const openImage = useRawImage(() => (openIsImage.value ? openRawUrl.value : ''))
  async function downloadOpenFile() {
    if (!openRawUrl.value || !openPath.value) return
    try {
      await downloadFile(openRawUrl.value, openPath.value.split('/').pop() || 'file')
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.downloadFailed')
    }
  }

  // 文件 state is per-source. openPath/fileDraft describe a file in the CURRENT
  // source's worktree, so switching source must drop them: carrying them over meant
  // the next 保存 wrote one worktree's draft into another's tree, at the same path.
  function resetFilePanel() {
    files.value = []
    openPath.value = null
    fileDraft.value = ''
    fileSaved.value = ''
    fileVersion.value = null
    fileBinary.value = false
    fileTooLarge.value = false
    fileBytes.value = 0
    fileEditable.value = false
    fileConflict.value = false
    tree.collapsedDirs.value = new Set()
  }

  // A directed open asked for by a <&path> chip. Whoever reaches loadFiles first
  // consumes it, so the listing can never auto-select the first file over the one
  // the reader actually clicked.
  let pendingOpen: string | null = null

  // 上一次指名要开的文件在这份清单里。`openFile` 拿它给调用者回话：没在——文件不在
  // 这一支的工作副本里，或者清单根本没拿到（任务找不到、项目没接仓库、取清单出错）——
  // 就该换个地方开它（工作面板退到「项目当前版本」那一格），而不是停在一格开不出它的
  // 「改动」上。
  let openFound = false

  // Two callers can ask for the listing in the same tick — `openFile` asks
  // directly, and coming on screen makes the activation watcher ask too. Letting
  // both run raced: whichever finished second re-ran the "nothing is open, select
  // the first file" branch and stole the file the reader had actually clicked.
  let filesInFlight: Promise<void> | null = null
  function loadFiles(opts: { silent?: boolean } = {}): Promise<void> {
    if (filesInFlight) return filesInFlight
    const p = doLoadFiles(opts).finally(() => {
      if (filesInFlight === p) filesInFlight = null
    })
    filesInFlight = p
    return p
  }

  async function doLoadFiles(opts: { silent?: boolean } = {}) {
    const tid = props.topicId
    const task = selectedTask.value
    const pid = props.projectId
    const epochAt = epoch()
    if (!tid || !pid || !task || sourceUnavailable.value || noRepo.value || noWorkYet.value) return
    const done = opts.silent ? null : startLoad()
    if (!opts.silent) errorMsg.value = null
    try {
      const listed = (await listFiles(pid, tid, task, fileSource.value)).data
      // Guard against a source switch mid-flight — without it the previous source's
      // listing repopulates the panel.
      if (selectedTask.value !== task || epoch() !== epochAt) return
      files.value = listed
      const want = pendingOpen
      if (want) {
        // 这个来源里没有这个文件时不要去读它：读回来的是一句后端的英文错误，它会把
        // 整块面板顶掉，而读者只是点了一枚 chip。记下这一笔，让调用者去别的地方开它
        // （工作面板退到「项目当前版本」那一格），列表留在原地。
        if (!listed.some((f) => f.path === want)) {
          pendingOpen = null
          return
        }
        openFound = true
        // Keep the directed path reserved while its read is in flight, so a
        // later diff/list response cannot start an automatic first-file read.
        await selectFile(want)
        if (epoch() === epochAt) pendingOpen = null
        return
      }
      // 打开的那一份不在这个来源里了：回到全部改动那一面，而不是对着一份读不到的文件。
      if (openPath.value && !listed.some((f) => f.path === openPath.value)) openPath.value = null
    } catch (e) {
      if (epoch() !== epochAt || opts.silent) return
      // 现场工作区没在读取预算里答上来：树上的文件来自差异，清单只是「打开其他
      // 文件」用的。拿一句请求超时把整格顶掉，比少一份清单糟得多；组合根会把来源
      // 切到已提交版本，再读一次这份清单。
      if (fileSource.value === 'live' && e instanceof RequestTimeoutError) return
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.loadFailed')
    } finally {
      done?.()
    }
  }

  // 回到全部改动那一面。没保存的修改照常暂存，回到这个文件还在。
  function closeFile() {
    keepDraft()
    fileRequest += 1
    openPath.value = null
    fileConflict.value = false
  }

  async function selectFile(path: string) {
    keepDraft()
    const request = ++fileRequest
    const epochAt = epoch()
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    if (!pid) return
    errorMsg.value = null
    fileConflict.value = false
    // Each file opens on its diff — that is what a review surface is for. Files
    // this topic never touched have no diff and open on their text.
    fileView.value = 'diff'
    const listed = files.value.find((f) => f.path === path)?.bytes ?? 0
    // Images render as images — Monaco would show mangled bytes.
    if (isImagePath(path)) {
      openPath.value = path
      fileDraft.value = ''
      fileSaved.value = ''
      fileVersion.value = null
      fileBinary.value = false
      fileTooLarge.value = false
      fileBytes.value = listed
      tree.revealInTree(path)
      return
    }
    try {
      const f = await readFile(pid, path, tid ?? undefined, task, fileSource.value)
      // A source switch mid-flight must not land the previous source's file — and
      // its draft — in the panel.
      if (selectedTask.value !== task || epoch() !== epochAt || fileRequest !== request) return
      openPath.value = path
      // Binary and oversized files arrive with no content: they open read-only,
      // so the draft stays empty and there is nothing to write back.
      fileDraft.value = f.content ?? ''
      fileSaved.value = f.content ?? ''
      fileVersion.value = f.version
      fileBinary.value = f.binary
      fileTooLarge.value = f.too_large
      fileBytes.value = f.bytes ?? listed
      fileEditable.value = f.editable !== false && f.source !== 'committed'
      const draft = drafts.get(draftKey(path))
      if (draft && !f.binary && !f.too_large) {
        fileDraft.value = draft.content
        fileSaved.value = draft.saved
        fileVersion.value = draft.version
        fileView.value = 'edit'
        fileConflict.value = f.version !== draft.version
      }
      tree.revealInTree(path)
    } catch (e) {
      if (selectedTask.value !== task || epoch() !== epochAt || fileRequest !== request) return
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    }
  }

  const editor = usePanelFileSave({
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
    reload: selectFile,
  })

  /** 换了来源或任务：这一代读回来的东西一样都不留。 */
  function resetSource() {
    fileRequest += 1
    filesInFlight = null
    pendingOpen = null
    openFound = false
    fileSaving.value = false
    resetFilePanel()
  }

  /** 指名要开的那一份：下一个读到清单的人负责把它开出来。 */
  function setPendingOpen(path: string | null) {
    pendingOpen = path
  }

  /** 上一次在这个来源里开过的那一份（换来源时回到它）。 */
  function lastFileForSource(): string | null {
    return lastFiles.get(sourceKey()) ?? null
  }

  /** 指名要开的那一份开出来了没有（`openFile` 拿它回话）。 */
  function hasOpenFileFound(): boolean {
    return openFound
  }

  return {
    files,
    openPath,
    fileDraft,
    fileSaved,
    fileSaving,
    fileDirty,
    fileToolReady,
    fileVersion,
    fileBinary,
    fileTooLarge,
    fileBytes,
    fileReadOnly,
    fileConflict,
    fileMerge,
    fileView,
    treeFiles: tree.treeFiles,
    collapsedDirs: tree.collapsedDirs,
    revealTick: tree.revealTick,
    openDiff,
    openDiffLines,
    effectiveView,
    openIsImage,
    openImageSrc: openImage.src,
    openImageError: openImage.error,
    draftCount: computed(() => drafts.size),
    keepDraft,
    loadFiles,
    selectFile,
    closeFile,
    downloadOpenFile,
    toggleDir: tree.toggleDir,
    saveFile: editor.saveFile,
    overwriteFile: editor.overwriteFile,
    resolveMerge: editor.resolveMerge,
    cancelMerge: editor.cancelMerge,
    reloadOpenFile: editor.reloadOpenFile,
    resetSource,
    setPendingOpen,
    lastFileForSource,
    hasOpenFileFound,
  }
}
