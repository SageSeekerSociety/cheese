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
//
// 这一份是组合根：任务与来源、谁在转圈、什么时候读，都在这里；四块各自成篇的取数
// 拆在 `panelChanges/` 下 —— 工作树差异、打开的那一份文件、文档的字节与修订、和
// 上一版的比较。返回的那一包和拆之前一模一样，组件与单测都不用动。
import type { FileSource, RoomTask } from '../cx_types'
import type { LiveSourceFallback, PanelChangesProps } from './panelChanges/types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getForgeConnection, listRoomTasks } from '../api'
import { phraseLabel } from '../lib/board'

import { usePanelDocument } from './panelChanges/documentPane'
import { usePanelFilePane } from './panelChanges/filePane'
import { usePanelGitChanges } from './panelChanges/gitChanges'
import { usePanelOfficeCompare } from './panelChanges/officeCompare'

import { t } from '@/i18n'

export type { PanelChangesProps } from './panelChanges/types'

/** 「改动」这一格的全部取数：状态进、动作出，一个组件都不碰。 */
export function usePanelChanges(props: PanelChangesProps) {
  const selectedTask = computed(() => props.taskId)
  const taskRow = ref<RoomTask | undefined>(undefined)
  const taskLoadError = ref<string | null>(null)
  const tasksLoaded = ref(false)
  let sourceEpoch = 0
  let taskRequest = 0
  const currentTask = computed(() => (taskRow.value?.id === selectedTask.value ? taskRow.value : undefined))
  // 任务结束了，它的文件就只读——跟在状态后面说。
  const sourceStatus = computed(() => {
    const task = currentTask.value
    const phrase = task?.presentation.phrase
    const agent = (task?.agent_handle && props.memberNames?.[task.agent_handle]) || props.agentName
    const status = phrase ? phraseLabel(phrase, agent) : ''
    return currentTask.value && currentTask.value.status !== 'open'
      ? t('work.room.changes.statusReadOnly', { status })
      : status
  })
  const sourceUnavailable = computed(() => tasksLoaded.value && !currentTask.value)
  // 还没有分支的任务：没有机器打开过它，它还没有改过任何东西。这是「暂无改动」，
  // 不是「打不开」，也不必去问。
  const noWorkYet = computed(() => !!currentTask.value && !currentTask.value.branch_name)
  // 没人挑过时按任务的状态定：待审阅时看交上来的那一版，审阅的就是它；现场的工作区
  // 那时可能已经不在了（云沙箱空闲一会儿就收回）。其余还开着的任务看现场。
  const requestedSource = ref<FileSource | null>(null)
  const fileSource = computed<FileSource>(() => {
    const task = currentTask.value
    if (task?.status !== 'open') return 'committed'
    return requestedSource.value ?? (task.card?.status === 'pending' ? 'committed' : 'live')
  })

  // 只读看着的这一件（它的分支、状态、卡），不读整个房间的任务清单。
  async function loadTasks() {
    const room = props.topicId
    const taskId = selectedTask.value
    const request = ++taskRequest
    if (!room) return
    taskLoadError.value = null
    try {
      const task = taskId
        ? (await listRoomTasks(room, { limit: 0, ids: [taskId] })).data.find((row) => row.id === taskId)
        : undefined
      if (request !== taskRequest) return
      taskRow.value = task
      tasksLoaded.value = true
    } catch (error) {
      if (request === taskRequest) {
        taskLoadError.value = error instanceof Error ? error.message : t('work.room.changes.loadTasksFailed')
      }
    }
  }

  const loading = ref(false)
  // 有几个读还在路上（这件任务、diff、文件列表）。哪一个先回来都不算完：转圈要等最后
  // 一个，不然先回来的那个把它关掉，剩下的还没到，面板就先说一句「暂无改动」。
  let inFlight = 0
  function startLoad(): () => void {
    const epoch = sourceEpoch
    inFlight += 1
    loading.value = true
    let done = false
    return () => {
      if (done || epoch !== sourceEpoch) return
      done = true
      inFlight -= 1
      if (inFlight === 0) loading.value = false
    }
  }
  // A background re-fetch: spins only the 刷新 button, never replaces the panel.
  const refreshing = ref(false)
  const errorMsg = ref<string | null>(null)
  // 现场工作区没及时答上来时，这一格退到了哪一面（见 `onLiveTimeout`）。
  const liveFallback = ref<LiveSourceFallback>('none')
  // 这一次前台读现场是不是超时了；`loadAll` 拿它在两次读都落地之后切来源。
  const liveTimeout = ref(false)
  // 见 checkRepo。
  const noRepo = ref(false)

  const gitChanges = usePanelGitChanges({
    props,
    task: selectedTask,
    source: fileSource,
    sourceUnavailable,
    noRepo,
    noWorkYet,
    refreshing,
    errorMsg,
    liveFallback,
    liveTimeout,
    epoch: () => sourceEpoch,
    startLoad,
  })

  const pane = usePanelFilePane({
    props,
    task: selectedTask,
    currentTask,
    source: fileSource,
    fileDiffs: gitChanges.fileDiffs,
    diffByPath: gitChanges.diffByPath,
    loading,
    sourceUnavailable,
    noRepo,
    noWorkYet,
    errorMsg,
    epoch: () => sourceEpoch,
    startLoad,
  })

  const doc = usePanelDocument({
    topicId: () => props.topicId,
    path: pane.openPath,
    version: pane.fileVersion,
    task: selectedTask,
    source: fileSource,
    readOnly: () =>
      props.readOnly === true || currentTask.value?.status !== 'open' || currentTask.value?.card?.status === 'pending',
    // 处理完一处修订：这份文件变了，树上的数字和 diff 都要重读。
    onChanged: async (path) => {
      if (path) await pane.selectFile(path)
      void gitChanges.loadGit({ silent: true })
    },
  })

  const compare = usePanelOfficeCompare({ task: selectedTask, path: pane.openPath })

  // ---- Loading policy: the surface loads when it comes on screen, the same rule
  // the drawer used ("opening the tool loads it"). One surface now, so both halves
  // load together — the tree cannot mark what the diff has not told it yet. ----
  async function loadAll(opts: { silent?: boolean; fresh?: boolean } = {}) {
    // 先问这件任务，再取 diff 和文件：问的这一下也算在转圈里。
    const done = opts.silent ? null : startLoad()
    try {
      await loadTasks()
      if (taskLoadError.value) return
      void checkRepo()
      if (noRepo.value) return
      // 两边都落地再决定退不退：现场那条路可能先超时（差异走机器），也可能文件
      // 清单先超时。等完再切来源，文件清单才不会落在上一代的请求上。
      await Promise.all([gitChanges.loadGit(opts), pane.loadFiles(opts)])
      await adoptCommittedFallback()
      // 读到了才记「这一格已经有东西了」：失败的那一次下次切回来还要重试。
      if (!errorMsg.value) loadedOnce.value = true
    } finally {
      done?.()
    }
  }

  // 现场工作区没在浏览器的读取预算里答上来：差异那边已经把已提交版本取回来了，
  // 这里把来源也切过去，后面的读文件、列文件才不会再撞同一次超时。摘要（差异）
  // 不再问一次。
  async function adoptCommittedFallback() {
    if (!liveTimeout.value || fileSource.value !== 'live') return
    requestedSource.value = 'committed'
    liveFallback.value = 'committed'
    await pane.loadFiles()
  }

  // 从「现场工作区没有及时响应」那条说明上重试：来源回到现场，两边重新读。
  async function retryLive() {
    requestedSource.value = null
    liveFallback.value = 'none'
    errorMsg.value = null
    await Promise.all([gitChanges.loadGit(), pane.loadFiles()])
    await adoptCommittedFallback()
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

  // 这一格读到过没有：读到了，切回来就接着用屏幕上那一份，不再整格清空重来。
  const loadedOnce = ref(false)
  // 不在这一格的时候有某一轮收工：回来得补一次静默重读，否则看的是旧差异。
  let missedTick = false

  watch(
    () => props.active,
    (on) => {
      if (!on) return
      if (!loadedOnce.value) {
        void loadAll()
        return
      }
      // 切回来：屏幕上那份留着（树、差异、滚动位置都在），不转圈、不重建。
      // 只有离开期间 AI 干完过一轮（下面的 `refreshTick`）才静默补读一次。
      if (missedTick) {
        missedTick = false
        void loadAll({ silent: true, fresh: true })
      }
    },
    { immediate: true }
  )

  // A turn ended: 芝士's commits and its working tree just changed.
  watch(
    () => props.refreshTick,
    () => {
      if (props.active) void loadAll({ silent: true, fresh: true })
      else missedTick = true
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
        void gitChanges.loadGit({ silent: true })
      }, REFRESH_MS)
    },
    { immediate: true }
  )
  onBeforeUnmount(stopAutoRefresh)

  function clearSource() {
    sourceEpoch += 1
    pane.resetSource()
    inFlight = 0
    loading.value = false
    gitChanges.clearGitDiff()
    errorMsg.value = null
    liveFallback.value = 'none'
    liveTimeout.value = false
    // 换了来源 / 换了任务：屏幕上那份已经不是这一份了，下次进来要重读。
    loadedOnce.value = false
    missedTick = false
  }

  async function selectVersion(source: FileSource) {
    if (fileSource.value === source) return
    const path = pane.openPath.value
    pane.keepDraft()
    clearSource()
    requestedSource.value = source
    pane.setPendingOpen(path ?? pane.lastFileForSource())
    await Promise.all([gitChanges.loadGit(), pane.loadFiles()])
    if (!errorMsg.value) loadedOnce.value = true
  }

  watch(
    () => props.taskId,
    (next, was) => {
      if (next === was) return
      pane.keepDraft()
      clearSource()
      taskRow.value = undefined
      requestedSource.value = null
      tasksLoaded.value = false
      if (props.active) void loadAll()
    }
  )

  // A <&path> chip (chat or doc) opens that file here. WorkPanel switches to this
  // tab first, then calls in. The file may be one this task never touched; it opens
  // all the same (the tree keeps listing what this task changed).
  //
  // 这一支的工作副本里根本没有它时，这里开不出来：回一句「没开着」，让 WorkPanel 换
  // 一格去开（项目当前版本）。一枚 chip 是读者指名要看的一份文件，不该因为没归到这件
  // 任务就点不开。
  async function openFile(path: string): Promise<boolean> {
    if (!tasksLoaded.value) await loadTasks()
    pane.keepDraft()
    clearSource()
    pane.setPendingOpen(path)
    await Promise.all([gitChanges.loadGit(), pane.loadFiles()])
    if (!errorMsg.value) loadedOnce.value = true
    return pane.hasOpenFileFound()
  }

  return {
    taskLoadError,
    selectedTask,
    currentTask,
    sourceStatus,
    sourceUnavailable,
    fileSource,
    fileToolReady: pane.fileToolReady,
    loading,
    refreshing,
    errorMsg,
    liveFallback,
    noRepo,
    fileDiffs: gitChanges.fileDiffs,
    diffByPath: gitChanges.diffByPath,
    treeFiles: pane.treeFiles,
    // 「打开其他文件」从这里挑：这个来源里的全部文件。
    allFiles: pane.files,
    openPath: pane.openPath,
    fileDraft: pane.fileDraft,
    fileSaved: pane.fileSaved,
    fileSaving: pane.fileSaving,
    fileDirty: pane.fileDirty,
    fileVersion: pane.fileVersion,
    fileBinary: pane.fileBinary,
    fileTooLarge: pane.fileTooLarge,
    fileBytes: pane.fileBytes,
    fileReadOnly: pane.fileReadOnly,
    fileConflict: pane.fileConflict,
    openDiff: pane.openDiff,
    openDiffLines: pane.openDiffLines,
    effectiveView: pane.effectiveView,
    fileView: pane.fileView,
    openIsImage: pane.openIsImage,
    openIsDocument: doc.openIsDocument,
    openDocumentType: doc.openDocumentType,
    revisionPath: doc.revisionPath,
    openImageSrc: pane.openImageSrc,
    openImageError: pane.openImageError,
    collapsedDirs: pane.collapsedDirs,
    revealTick: pane.revealTick,
    draftCount: pane.draftCount,
    docBytes: doc.docBytes,
    docLoading: doc.docLoading,
    docError: doc.docError,
    docRendererMissing: doc.docRendererMissing,
    // 这一份 .docx 的修订（`useDocumentRevisions.ts`）
    revs: doc.revs,
    canCompare: compare.canCompare,
    comparing: compare.comparing,
    comparison: compare.comparison,
    comparisonLoading: compare.comparisonLoading,
    comparisonError: compare.comparisonError,
    toggleCompare: compare.toggleCompare,
    // 动作
    loadAll,
    retryLive,
    selectFile: pane.selectFile,
    closeFile: pane.closeFile,
    selectVersion,
    openFile,
    toggleDir: pane.toggleDir,
    downloadOpenFile: pane.downloadOpenFile,
    saveFile: pane.saveFile,
    overwriteFile: pane.overwriteFile,
    fileMerge: pane.fileMerge,
    resolveMerge: pane.resolveMerge,
    cancelMerge: pane.cancelMerge,
    reloadOpenFile: pane.reloadOpenFile,
    onRevisionDecided: doc.onRevisionDecided,
  }
}

/** 「改动」这一格的取数原样递给面板（props）：面板自己不认识接口。 */
export type PanelChangesBundle = ReturnType<typeof usePanelChanges>
