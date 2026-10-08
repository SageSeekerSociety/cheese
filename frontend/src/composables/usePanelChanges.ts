// 任务「改动」那一格的取数：它调的那些接口、轮询、读到写、草稿与冲突。只看这一件
// 任务：别的任务在它们自己的页面上，项目的全部文件在「范围 → 全部文件」里。
//
// 和画的那一半（`components/panels/PanelChangesView.vue`）分家的理由，和 UserRef
// 那次一样：这一格原先自己 import 十个接口函数、自己按 20 秒轮询、自己读
// `beforeunload`，于是「改动」这个界面在测试和演示里都必须先有一个假后端
// （`views/demo/demoPanels.ts` 就是为它写的十几条路由）。现在这一层在这里，展示
// 组件只认 props、只往上发事件 —— `/demo` 里那条假后端照旧能喂它，因为喂的还是
// 同一个 fetch 层。
//
// 组件那一半拿到的每一样东西都是这里算好的：连「来源标题写什么字」这类字符串也在
// 这里，因为它们的判据（活还在不在跑、文件是不是只读）全是取数那一边的事实。
import type { FileSource, RoomTask, WorkspaceFile } from '../cx_types'
import type { FileDiff } from '../lib/diff'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  ApiError,
  downloadFile,
  getForgeConnection,
  getGitDiff,
  listFiles,
  readFile,
  workspaceFileRawUrl,
  writeFile,
} from '../api'
import { phraseLabel } from '../lib/board'
import { parseDiffLines, splitDiffByFile } from '../lib/diff'
import { useDocumentBytes } from '../lib/documentBytes'
import { DOCUMENT_TYPES, needsDocumentView, suffixOf } from '../lib/fileKind'
import { fetchRoomTasks } from '../lib/topicPanelCache'

import { useDocumentRevisions } from './useDocumentRevisions'
import { useTopicMemory } from './useTopicMemory'

import { t } from '@/i18n'

export interface PanelChangesProps {
  topicId: string | null
  projectId: string | null
  /** 这一格看的那件任务。 */
  taskId: string | null
  readOnly?: boolean
  /** This tab is the one on screen. Loads happen on the rising edge, exactly
   *  like opening the old drawer did. */
  active?: boolean
  /** Bumped by WorkPanel when a turn ends — the moment 芝士's commits and its
   *  working tree actually changed. Silent re-fetch, never a spinner. */
  refreshTick?: number
}

/** 「改动」这一格的全部取数：状态进、动作出，一个组件都不碰。 */
export function usePanelChanges(props: PanelChangesProps) {
  const selectedTask = computed(() => props.taskId)
  const taskRow = ref<RoomTask | undefined>(undefined)
  const taskLoadError = ref<string | null>(null)
  const tasksLoaded = ref(false)
  let sourceEpoch = 0
  let fileRequest = 0
  let taskRequest = 0
  const currentTask = computed(() => (taskRow.value?.id === selectedTask.value ? taskRow.value : undefined))
  // 任务结束了，它的文件就只读——跟在状态后面说。
  const sourceStatus = computed(() => {
    const phrase = currentTask.value?.presentation.phrase
    const status = phrase ? phraseLabel(phrase) : ''
    return currentTask.value && currentTask.value.status !== 'open'
      ? t('work.room.changes.statusReadOnly', { status })
      : status
  })
  const sourceUnavailable = computed(() => tasksLoaded.value && !currentTask.value)
  const requestedSource = ref<FileSource>('live')
  const fileSource = computed<FileSource>(() =>
    currentTask.value?.status === 'open' ? requestedSource.value : 'committed'
  )

  async function loadTasks(opts: { fresh?: boolean } = {}) {
    const room = props.topicId
    const request = ++taskRequest
    if (!room) return
    taskLoadError.value = null
    try {
      const tasks = await fetchRoomTasks(room, opts)
      if (request !== taskRequest) return
      taskRow.value = tasks.data.find((row) => row.id === selectedTask.value && !!row.branch_name)
      tasksLoaded.value = true
    } catch (error) {
      if (request === taskRequest) {
        taskLoadError.value = error instanceof Error ? error.message : t('work.room.changes.loadTasksFailed')
      }
    }
  }

  // 打开的文件看哪一面：它的 diff，还是可编辑的全文。
  type FileView = 'diff' | 'edit'
  const fileView = ref<FileView>('diff')

  const loading = ref(false)
  // A background re-fetch: spins only the 刷新 button, never replaces the panel.
  const refreshing = ref(false)
  const errorMsg = ref<string | null>(null)
  // 见 checkRepo。
  const noRepo = ref(false)
  // 一枚 chip 指来的文件，在当前这个来源里找不到。不是这块面板出了错，所以它不走
  // `errorMsg`——那一句的样子是「这一格加载失败」。
  const missing = ref<string | null>(null)
  async function downloadOpenFile() {
    if (!openRawUrl.value || !openPath.value) return
    try {
      await downloadFile(openRawUrl.value, openPath.value.split('/').pop() || 'file')
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.downloadFailed')
    }
  }

  // ---- Git: the working-tree diff ----
  // 提交记录不在这里读：审阅看的是改了什么，提交是过程记录。
  const gitDiff = ref<string>('')

  async function loadGit(opts: { silent?: boolean } = {}) {
    const tid = props.topicId
    const task = selectedTask.value
    const pid = props.projectId
    const epoch = sourceEpoch
    if (!tid || !pid || !task || sourceUnavailable.value || noRepo.value) return
    if (opts.silent) refreshing.value = true
    else loading.value = true
    errorMsg.value = null
    try {
      const diff = await getGitDiff(pid, tid, task, fileSource.value)
      // Guard against a source switch mid-flight.
      if (selectedTask.value !== task || sourceEpoch !== epoch) return
      gitDiff.value = diff.diff
    } catch (e) {
      if (sourceEpoch === epoch) errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.loadFailed')
    } finally {
      if (selectedTask.value === task && sourceEpoch === epoch) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  // 一份 diff，切成每个文件一段。树上的 +N −M、点开文件看的那一段，都读这里 ——
  // 不再多要一次请求，也不会出现「树说改了、diff 里没有」这种两边不一致。
  const fileDiffs = computed<FileDiff[]>(() => splitDiffByFile(gitDiff.value))
  const diffByPath = computed(() => new Map(fileDiffs.value.map((f) => [f.path, f])))

  // ---- 文件: a two-pane browser — the tree stays visible on the left, the opened
  // file loads on the right: its diff, or the editable text (save = 人改文件
  // 即指令). ----
  const files = ref<WorkspaceFile[]>([])
  const openPath = ref<string | null>(null)
  const fileDraft = ref<string>('')
  const fileSaved = ref<string>('') // last loaded/saved content, for the dirty flag
  const fileSaving = ref(false)
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

  // 文件树: 「这一支碰过哪些文件、工作区里现在还有哪些」在这里定，折成一层层文件夹
  // 那件事在 lib/changesTree.ts（纯函数，没有组件）。
  // 改动清单是一份要逐个看完的清单，文件夹默认都开着；收起的那几个记在这里。
  const collapsedDirs = ref(new Set<string>())
  function toggleDir(path: string) {
    const next = new Set(collapsedDirs.value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    collapsedDirs.value = next
  }
  // 定位: when a file is opened by path (e.g. clicking a <&path> chip in chat or
  // the doc), reopen every ancestor folder so the tree shows where it lives. 树
  // 自己负责把那一行滚进视野——那是画的事，它听 revealTick。
  const revealTick = ref(0)
  function revealInTree(path: string) {
    const parts = path.split('/')
    if (parts.length > 1) {
      const next = new Set(collapsedDirs.value)
      let prefix = ''
      for (const part of parts.slice(0, -1)) {
        prefix = prefix ? `${prefix}/${part}` : part
        next.delete(prefix)
      }
      collapsedDirs.value = next
    }
    revealTick.value += 1
  }
  // 树上列的是这一支改到的文件。改动清单里可能有工作区已经没有的文件（这一支删掉了
  // 它）——那也是要审阅的一条，所以清单按 diff 列，不按工作区。
  const treeFiles = computed<WorkspaceFile[]>(
    () =>
      fileDiffs.value.map((d) => ({
        path: d.path,
        bytes: files.value.find((f) => f.path === d.path)?.bytes ?? 0,
      })) as WorkspaceFile[]
  )

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

  // ---- 文档: 这一版画出来，外加它自己带的修订 ----
  // 一份 .docx 的差异是一句「二进制文件不同」——按文件类型分派渲染器之前，这一格对一
  // 份交付的文档能说的只有这句话。现在它画出这一版的页面，再把文件里的修订逐条列出
  // 来：那才是「这一版比上一版改了什么」在一份 Word 文档里的真实形态。
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
    topicId: () => props.topicId,
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
    if (path) await selectFile(path)
    void loadGit({ silent: true })
  }

  // 这一份 .docx 的修订：清单、只读怎么判、处理完干什么，都在那一份里。处理完这条活
  // 的文件就变了（`onRevisionDecided`）—— 这一格除了那一处，还要把树上的数字重读。
  const revs = useDocumentRevisions(
    {
      topicId: () => props.topicId,
      path: () => revisionPath.value,
      version: () => fileVersion.value,
      task: () => selectedTask.value,
      source: () => fileSource.value,
      readOnly: () => props.readOnly === true || currentTask.value?.status !== 'open',
    },
    { onDecided: () => void onRevisionDecided() }
  )

  // Raw bytes of the open file: what <img> renders for an image, and what the
  // download button hands over for anything else that can't be shown as text.
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
    collapsedDirs.value = new Set()
  }

  // A directed open asked for by a <&path> chip. Whoever reaches loadFiles first
  // consumes it, so the listing can never auto-select the first file over the one
  // the reader actually clicked.
  let pendingOpen: string | null = null

  // Two callers can ask for the listing in the same tick — `openFile` asks
  // directly, and coming on screen makes the activation watcher ask too. Letting
  // both run raced: whichever finished second re-ran the "nothing is open, select
  // the first file" branch and stole the file the reader had actually clicked.
  let filesInFlight: Promise<void> | null = null
  function loadFiles(): Promise<void> {
    if (filesInFlight) return filesInFlight
    const p = doLoadFiles().finally(() => {
      if (filesInFlight === p) filesInFlight = null
    })
    filesInFlight = p
    return p
  }

  async function doLoadFiles() {
    const tid = props.topicId
    const task = selectedTask.value
    const pid = props.projectId
    const epoch = sourceEpoch
    if (!tid || !pid || !task || sourceUnavailable.value || noRepo.value) return
    loading.value = true
    errorMsg.value = null
    try {
      const listed = (await listFiles(pid, tid, task, fileSource.value)).data
      // Guard against a source switch mid-flight — without it the previous source's
      // listing repopulates the panel.
      if (selectedTask.value !== task || sourceEpoch !== epoch) return
      files.value = listed
      const want = pendingOpen
      if (want) {
        // 这个来源里没有这个文件时不要去读它：读回来的是一句后端的英文错误，它会把
        // 整块面板顶掉，而读者只是点了一枚 chip。说清它不在这里，列表留在原地。
        if (!listed.some((f) => f.path === want)) {
          pendingOpen = null
          missing.value = want
          return
        }
        // Keep the directed path reserved while its read is in flight, so a
        // later diff/list response cannot start an automatic first-file read.
        await selectFile(want)
        if (sourceEpoch === epoch) pendingOpen = null
        return
      }
      // 打开的那一份不在这个来源里了：回到全部改动那一面，而不是对着一份读不到的文件。
      if (openPath.value && !listed.some((f) => f.path === openPath.value)) openPath.value = null
    } catch (e) {
      if (sourceEpoch !== epoch) return
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.loadFailed')
    } finally {
      if (selectedTask.value === task && sourceEpoch === epoch) loading.value = false
    }
  }

  // 回到全部改动那一面。没保存的修改照常暂存，回到这个文件还在。
  function closeFile() {
    keepDraft()
    fileRequest += 1
    openPath.value = null
    fileConflict.value = false
    missing.value = null
  }

  async function selectFile(path: string) {
    keepDraft()
    missing.value = null
    const request = ++fileRequest
    const epoch = sourceEpoch
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
      revealInTree(path)
      return
    }
    try {
      const f = await readFile(pid, path, tid ?? undefined, task, fileSource.value)
      // A source switch mid-flight must not land the previous source's file — and
      // its draft — in the panel.
      if (selectedTask.value !== task || sourceEpoch !== epoch || fileRequest !== request) return
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
      revealInTree(path)
    } catch (e) {
      if (selectedTask.value !== task || sourceEpoch !== epoch || fileRequest !== request) return
      errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    }
  }

  // Every save carries the version on which the user's decision was based.
  async function writeOpenFile(expected: string | null) {
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    const path = openPath.value
    if (!pid || !path || !selectedTask.value || fileReadOnly.value || !fileDirty.value || fileSaving.value) return
    const draft = fileDraft.value
    const key = draftKey(path)
    const epoch = sourceEpoch
    fileSaving.value = true
    errorMsg.value = null
    try {
      const res = await writeFile(pid, path, draft, tid ?? undefined, expected, task)
      if (drafts.get(key)?.content === draft) drafts.delete(key)
      // The answer is only about the file that was open in the topic that was
      // open — anything else finished after a switch and must be dropped.
      if (selectedTask.value !== task || openPath.value !== path || sourceEpoch !== epoch) return
      fileSaved.value = draft
      fileVersion.value = res.version
      fileConflict.value = false
    } catch (e) {
      if (selectedTask.value !== task || openPath.value !== path || sourceEpoch !== epoch) return
      if (e instanceof ApiError && e.status === 409) {
        // 芝士 wrote this file since it was read. Neither side wins by default:
        // show the conflict and let the human reload or overwrite on purpose.
        fileConflict.value = true
      } else {
        errorMsg.value = e instanceof Error ? e.message : t('work.room.changes.saveFailed')
      }
    } finally {
      if (selectedTask.value === task && sourceEpoch === epoch) fileSaving.value = false
    }
  }

  function saveFile() {
    void writeOpenFile(fileVersion.value)
  }

  // 冲突后的两条出路,都由人点：丢掉自己的改动看最新的，或者明知有冲突仍然覆盖。
  async function overwriteFile() {
    const pid = props.projectId
    const tid = props.topicId
    const task = selectedTask.value
    const path = openPath.value
    const epoch = sourceEpoch
    if (!pid || !path || fileReadOnly.value) return
    try {
      const current = await readFile(pid, path, tid, task, 'live')
      if (epoch !== sourceEpoch || openPath.value !== path) return
      await writeOpenFile(current.version)
    } catch (error) {
      if (epoch === sourceEpoch)
        errorMsg.value = error instanceof Error ? error.message : t('work.room.changes.readFileFailed')
    }
  }

  function reloadOpenFile() {
    const path = openPath.value
    if (path) {
      drafts.delete(draftKey(path))
      fileDraft.value = fileSaved.value
      void selectFile(path)
    }
  }

  // ---- Loading policy: the surface loads when it comes on screen, the same rule
  // the drawer used ("opening the tool loads it"). One surface now, so both halves
  // load together — the tree cannot mark what the diff has not told it yet. ----
  async function loadAll(opts: { silent?: boolean; fresh?: boolean } = {}) {
    await loadTasks({ fresh: opts.fresh })
    if (taskLoadError.value) return
    void checkRepo()
    if (noRepo.value) return
    void loadGit(opts)
    void loadFiles()
  }

  // 项目没接代码仓库时，文件和提交记录都拿不到，后端答的是一句「项目没有代码仓库」。
  // 那不是这一格出了错，是这一格本来就没有东西，所以问一声，照空状态说，不把它当报错
  // 挂出来。和取文件同时问，不排在它前面：有仓库的项目（绝大多数）不该为这一问多等
  // 一个来回。问不到就当有仓库：真出了错，那句错还得让人看见。每个项目只问一次。
  let repoCheckedFor: string | null = null
  async function checkRepo() {
    const pid = props.projectId
    if (!pid || repoCheckedFor === pid) return
    repoCheckedFor = pid
    try {
      const forge = await getForgeConnection(pid)
      if (props.projectId === pid) noRepo.value = !forge.connected
    } catch {
      if (props.projectId === pid) noRepo.value = false
    }
  }

  watch(
    () => props.active,
    (on) => {
      if (on) loadAll()
    },
    { immediate: true }
  )

  // A turn ended: 芝士's commits and its working tree just changed.
  watch(
    () => props.refreshTick,
    () => {
      if (props.active) loadAll({ silent: true, fresh: true })
    }
  )

  // Panels that go stale while you watch them: 芝士 commits mid-look and the Git
  // view still shows the moment it was opened. Re-fetch on a timer while it is on
  // screen. (资源 used to poll on this same timer and no longer exists as a tab —
  // its numbers now load once, when the header popover is opened.)
  const REFRESH_MS = 20_000
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  function stopAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer)
    refreshTimer = null
  }
  watch(
    () => props.active,
    (on) => {
      stopAutoRefresh()
      if (!on) return
      refreshTimer = setInterval(() => {
        // A hidden tab polling forever is pure waste — it re-fetches on the next
        // tick after it comes back anyway.
        if (typeof document !== 'undefined' && document.hidden) return
        // Commits and the diff only. The listing changes when a turn writes
        // files, which the turn-boundary tick already covers — putting it on the
        // timer would be a third request every 20 seconds buying nothing.
        void loadGit({ silent: true })
      }, REFRESH_MS)
    },
    { immediate: true }
  )
  onBeforeUnmount(stopAutoRefresh)

  function clearSource() {
    sourceEpoch += 1
    fileRequest += 1
    filesInFlight = null
    pendingOpen = null
    fileSaving.value = false
    loading.value = false
    gitDiff.value = ''
    errorMsg.value = null
    missing.value = null
    resetFilePanel()
  }

  async function selectVersion(source: FileSource) {
    if (fileSource.value === source) return
    const path = openPath.value
    keepDraft()
    clearSource()
    requestedSource.value = source
    pendingOpen = path ?? lastFiles.get(sourceKey()) ?? null
    await Promise.all([loadGit(), loadFiles()])
  }

  watch(
    () => props.taskId,
    (next, was) => {
      if (next === was) return
      keepDraft()
      clearSource()
      taskRow.value = undefined
      tasksLoaded.value = false
      if (props.active) void loadAll()
    }
  )

  // A <&path> chip (chat or doc) opens that file here. WorkPanel switches to this
  // tab first, then calls in. The file may be one this task never touched; it opens
  // all the same (the tree keeps listing what this task changed).
  async function openFile(path: string) {
    if (!tasksLoaded.value) await loadTasks()
    keepDraft()
    clearSource()
    pendingOpen = path
    await Promise.all([loadGit(), loadFiles()])
  }

  return {
    taskLoadError,
    selectedTask,
    currentTask,
    sourceStatus,
    sourceUnavailable,
    fileSource,
    fileToolReady,
    loading,
    refreshing,
    errorMsg,
    noRepo,
    missing,
    fileDiffs,
    diffByPath,
    treeFiles,
    // 「打开其他文件」从这里挑：这个来源里的全部文件。
    allFiles: files,
    openPath,
    fileDraft,
    fileSaved,
    fileSaving,
    fileDirty,
    fileVersion,
    fileBinary,
    fileTooLarge,
    fileBytes,
    fileReadOnly,
    fileConflict,
    openDiff,
    openDiffLines,
    effectiveView,
    fileView,
    openIsImage,
    openIsDocument,
    openDocumentType,
    revisionPath,
    openRawUrl,
    collapsedDirs,
    revealTick,
    draftCount: computed(() => drafts.size),
    docBytes,
    docLoading,
    docError,
    docRendererMissing,
    // 这一份 .docx 的修订（`useDocumentRevisions.ts`）
    revs,
    // 动作
    loadAll,
    selectFile,
    closeFile,
    selectVersion,
    openFile,
    toggleDir,
    downloadOpenFile,
    saveFile,
    overwriteFile,
    reloadOpenFile,
    onRevisionDecided,
  }
}

/** 「改动」这一格的取数原样递给面板（props）：面板自己不认识接口。 */
export type PanelChangesBundle = ReturnType<typeof usePanelChanges>
