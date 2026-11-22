// 「改动」这一格打开着的那一份文件：状态、清单、读取和「能不能编辑」的判据。
//
// 这一格只看、只审：编辑在任务的「文件」那一格（`useTaskFiles`），这里只回答这份文件
// 能不能去那里编辑。工作树差异在 `gitChanges.ts`，文件树在 `fileTree.ts`，文档的字节
// 与修订在 `documentPane.ts`，和上一版的比较在 `officeCompare.ts`；任务、来源和加载
// 策略还在组合根 `../usePanelChanges.ts`。
import type { ComputedRef, Ref } from 'vue'
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'
import type { PanelChangesProps } from './types'

import { computed, ref } from 'vue'

import { downloadFile, listFiles, readFile, RequestTimeoutError, workspaceFileRawUrl } from '../../api'
import { parseDiffLines } from '../../lib/diff'
import { useRawImage } from '../useRawImage'
import { useTopicMemory } from '../useTopicMemory'

import { usePanelFileTree } from './fileTree'

import { t } from '@/i18n'

/** 打开的文件看哪一面：它的 diff，还是全文。 */
export type FileView = 'diff' | 'text'

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
  // file loads on the right: its diff, or its text. ----
  const files = ref<WorkspaceFile[]>([])
  const openPath = ref<string | null>(null)
  const fileText = ref<string>('')
  const fileView = ref<FileView>('diff')
  // 每一次「打开哪一份」的序号：路上的读回来时对不上就丢掉。
  let fileRequest = 0
  // 横条上关于「这一份文件」的那半（路径、差异/编辑、保存）只在文件区真的摆出来时才有。
  const fileToolReady = computed(() => !sourceUnavailable.value && !noRepo.value && !loading.value && !errorMsg.value)
  // Version of the open file as it was read (a document's revisions are keyed by it).
  const fileVersion = ref<string | null>(null)
  // Files that must not be edited as text: binary (a text round-trip destroys
  // them) or too large to send.
  const fileBinary = ref(false)
  const fileTooLarge = ref(false)
  const fileBytes = ref(0)
  // The file cannot be edited in the task's「文件」panel either: the bar says
  // 只读 instead of offering 编辑.
  const fileReadOnly = computed(
    () =>
      props.readOnly ||
      fileSource.value === 'committed' ||
      currentTask.value?.status !== 'open' ||
      // 待审阅时不直接改：审的这一版会在审的过程中变掉。想改的地方写成修改建议。
      currentTask.value?.card?.status === 'pending' ||
      fileBinary.value ||
      fileTooLarge.value ||
      openIsImage.value
  )
  // The file last open in each source: switching back to the source reopens it.
  const { lastFiles } = useTopicMemory()
  function sourceKey() {
    return `${selectedTask.value}:${fileSource.value}`
  }
  function keepOpenFile() {
    if (openPath.value) lastFiles.set(sourceKey(), openPath.value)
  }

  const tree = usePanelFileTree({ fileDiffs, files })

  /** The open file's own diff, or null when this topic did not touch it. */
  const openDiff = computed<FileDiff | null>(() =>
    openPath.value ? diffByPath.value.get(openPath.value) ?? null : null
  )
  const openDiffLines = computed(() => (openDiff.value ? parseDiffLines(openDiff.value.body) : []))
  // 差异 is the default face of a changed file — reviewing is what this tab is
  // for — but a file with no diff has only one face, so the toggle is not offered.
  const effectiveView = computed<FileView>(() => (openDiff.value ? fileView.value : 'text'))

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
    // 成功和失败各弹一句，都在 `downloadFile` 那一层（`lib/download`）。这里的
    // `errorMsg` 还给读文件、切源那些事用，下载不再往它上面写。
    await downloadFile(openRawUrl.value, openPath.value.split('/').pop() || 'file')
  }

  // 文件 state is per-source. openPath/fileText describe a file in the CURRENT
  // source's worktree, so switching source must drop them.
  function resetFilePanel() {
    files.value = []
    openPath.value = null
    fileText.value = ''
    fileVersion.value = null
    fileBinary.value = false
    fileTooLarge.value = false
    fileBytes.value = 0
    tree.collapsedDirs.value = new Set()
  }

  // The file to reopen once the listing of the newly chosen source is read.
  // Whoever reaches loadFiles first consumes it, so the listing can never
  // auto-select another file over it.
  let pendingOpen: string | null = null

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

  // 回到全部改动那一面。
  function closeFile() {
    keepOpenFile()
    fileRequest += 1
    openPath.value = null
  }

  async function selectFile(path: string) {
    keepOpenFile()
    const request = ++fileRequest
    const epochAt = epoch()
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    if (!pid) return
    errorMsg.value = null
    // Each file opens on its diff — that is what a review surface is for. Files
    // this topic never touched have no diff and open on their text.
    fileView.value = 'diff'
    const listed = files.value.find((f) => f.path === path)?.bytes ?? 0
    // Images render as images — Monaco would show mangled bytes.
    if (isImagePath(path)) {
      openPath.value = path
      fileText.value = ''
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
      // Binary and oversized files arrive with no content.
      fileText.value = f.content ?? ''
      fileVersion.value = f.version
      fileBinary.value = f.binary
      fileTooLarge.value = f.too_large
      fileBytes.value = f.bytes ?? listed
      tree.revealInTree(path)
    } catch (e) {
      if (selectedTask.value !== task || epoch() !== epochAt || fileRequest !== request) return
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    }
  }

  /** 换了来源或任务：这一代读回来的东西一样都不留。 */
  function resetSource() {
    fileRequest += 1
    filesInFlight = null
    pendingOpen = null
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

  return {
    files,
    openPath,
    fileText,
    fileToolReady,
    fileVersion,
    fileBinary,
    fileTooLarge,
    fileBytes,
    fileReadOnly,
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
    keepOpenFile,
    loadFiles,
    selectFile,
    closeFile,
    downloadOpenFile,
    toggleDir: tree.toggleDir,
    resetSource,
    setPendingOpen,
    lastFileForSource,
  }
}
