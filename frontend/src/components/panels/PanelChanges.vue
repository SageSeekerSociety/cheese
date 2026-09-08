<script setup lang="ts">
// 改动 tab: 这个话题干出来的东西，一个面看完。
//
// 它以前是两个半成品并排放着，中间一个分段开关：Git 那半是一坨没有语法着色、不能
// 按文件跳的裸 diff，文件那半是一棵不知道哪些文件被改过的树。想验收的人得先在
// 「改动」里读整块 diff 找出改了哪些文件，再切到「文件」里一个个翻出来看——两边
// 都不是一个能验收的面。
//
// 合成之后只有一棵树：树上标着每个文件改了多少，点开看的是这个文件自己的 diff，
// 要微调就切到编辑（保存冲突的两条出路原样保留）。分段开关没了。
import type { GitCommit, WorkspaceFile } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import {
  ApiError,
  downloadFile,
  getGitDiff,
  getGitLog,
  listFiles,
  readFile,
  workspaceFileRawUrl,
  writeFile,
} from '../../api'
import { parseDiffLines, splitDiffByFile } from '../../lib/diff'
import CodeEditor from '../CodeEditor.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    // This tab is the one on screen. Loads happen on the rising edge, exactly
    // like opening the old drawer did.
    active?: boolean
    // Bumped by WorkPanel when a turn ends — the moment 芝士's commits and its
    // working tree actually changed. Silent re-fetch, never a spinner.
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const { mdAndUp } = useDisplay()

// 树的范围: 默认只看这个话题改过的文件——验收要看的就是这些。展开成全部文件是
// 为了「看一眼旁边那个文件原来长什么样」，那是次要动作。
const showAll = ref(false)
// 打开的文件看哪一面：它的 diff，还是可编辑的全文。
type FileView = 'diff' | 'edit'
const fileView = ref<FileView>('diff')

const loading = ref(false)
// A background re-fetch: spins only the 刷新 button, never replaces the panel.
const refreshing = ref(false)
const errorMsg = ref<string | null>(null)
async function downloadOpenFile() {
  if (!openRawUrl.value || !openPath.value) return
  try {
    await downloadFile(openRawUrl.value, openPath.value.split('/').pop() || 'file')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '下载失败'
  }
}

// ---- Git: commit log + working-tree diff ----
const gitCommits = ref<GitCommit[]>([])
const gitDiff = ref<string>('')

async function loadGit(opts: { silent?: boolean } = {}) {
  const tid = props.topicId
  const pid = props.projectId
  if (!tid || !pid) return
  if (opts.silent) refreshing.value = true
  else loading.value = true
  errorMsg.value = null
  try {
    // A fresh repo with no commits makes git log fail (422); tolerate it so the
    // diff still renders instead of the whole panel showing an error. Always
    // topic-scoped: the project-level answer is OTHER topics' commits (before
    // 采纳 this topic's commits live only on its branch; after, the base is
    // everyone's).
    const [log, diff] = await Promise.all([
      getGitLog(pid, tid).catch(() => ({ data: [] as GitCommit[], total: 0 })),
      getGitDiff(pid, tid),
    ])
    // Guard against a topic switch mid-flight.
    if (props.topicId !== tid) return
    gitCommits.value = log.data
    gitDiff.value = diff.diff
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topicId === tid) {
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
const fileListOpen = ref(true) // the ☰ toggle hides the list for a wider editor
const fileDirty = computed(() => fileDraft.value !== fileSaved.value)
// Version of the open file as it was read; echoed back on save so a write that
// lost a race to 芝士 is rejected instead of silently erasing their edits.
const fileVersion = ref<string | null>(null)
// Files that must not be edited as text: binary (a text round-trip destroys
// them) or too large to send. They open read-only, with no 保存 button.
const fileBinary = ref(false)
const fileTooLarge = ref(false)
const fileBytes = ref(0)
const fileReadOnly = computed(() => fileBinary.value || fileTooLarge.value || openIsImage.value)
// Set when the backend rejected a save as a conflict. Nobody wins by default —
// the human sees it and picks.
const fileConflict = ref(false)

// 文件树: the backend returns a flat list of full relative paths; build a nested
// tree out of it (folders first, each level sorted by name), then flatten into
// render rows — only expanded folders contribute their subtrees.
//
// 默认只装这个话题改过的文件，并且全展开：那是一份清单，收起来等于把要验收的东西
// 藏起来。切到全部文件时它才变回一棵默认收起的树。
interface FileRow {
  type: 'dir' | 'file'
  path: string // full relative path (dir or file)
  name: string // last segment, what we display
  depth: number
  bytes: number
  /** Set when this topic's branch touches the file — the marker on the row. */
  diff?: FileDiff
}
const expandedDirs = ref(new Set<string>())
function toggleDir(path: string) {
  const next = new Set(expandedDirs.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedDirs.value = next
}
// 定位: when a file is opened by path (e.g. clicking a <&path> chip in chat or
// the doc), expand every ancestor folder so the tree shows where it lives, then
// scroll the highlighted row into view.
const fileListEl = ref<HTMLElement | null>(null)
function revealInTree(path: string) {
  const parts = path.split('/')
  if (parts.length > 1) {
    const next = new Set(expandedDirs.value)
    let prefix = ''
    for (const part of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${part}` : part
      next.add(prefix)
    }
    expandedDirs.value = next
  }
  void nextTick(() => {
    fileListEl.value?.querySelector('.file-item--active')?.scrollIntoView({ block: 'nearest' })
  })
}
// 改动清单里可能有工作区已经没有的文件（这一支删掉了它）——那也是要验收的一条，
// 不能因为树是按工作区建的就漏掉。
const treeFiles = computed<WorkspaceFile[]>(() => {
  if (!showAll.value) {
    return fileDiffs.value.map((d) => ({
      path: d.path,
      bytes: files.value.find((f) => f.path === d.path)?.bytes ?? 0,
    })) as WorkspaceFile[]
  }
  const known = new Set(files.value.map((f) => f.path))
  const gone = fileDiffs.value
    .filter((d) => !known.has(d.path))
    .map((d) => ({ path: d.path, bytes: 0 }) as WorkspaceFile)
  return [...files.value, ...gone]
})

const fileRows = computed<FileRow[]>(() => {
  interface DirNode {
    dirs: Map<string, DirNode>
    files: WorkspaceFile[]
  }
  const root: DirNode = { dirs: new Map(), files: [] }
  for (const f of treeFiles.value) {
    const parts = f.path.split('/')
    let node = root
    for (const part of parts.slice(0, -1)) {
      let child = node.dirs.get(part)
      if (!child) {
        child = { dirs: new Map(), files: [] }
        node.dirs.set(part, child)
      }
      node = child
    }
    node.files.push(f)
  }
  const rows: FileRow[] = []
  const walk = (node: DirNode, prefix: string, depth: number) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const path = prefix ? `${prefix}/${name}` : name
      rows.push({ type: 'dir', path, name, depth, bytes: 0 })
      // A changed-files list is a checklist, so it is always open; the full tree
      // stays collapsed by default (open exactly what you need).
      if (!showAll.value || expandedDirs.value.has(path)) {
        walk(node.dirs.get(name)!, path, depth + 1)
      }
    }
    const sorted = [...node.files].sort((a, b) => a.path.localeCompare(b.path))
    for (const f of sorted) {
      rows.push({
        type: 'file',
        path: f.path,
        name: f.path.split('/').pop() ?? f.path,
        depth,
        bytes: f.bytes,
        diff: diffByPath.value.get(f.path),
      })
    }
  }
  walk(root, '', 0)
  return rows
})

/** The open file's own diff, or null when this topic did not touch it. */
const openDiff = computed<FileDiff | null>(() => (openPath.value ? diffByPath.value.get(openPath.value) ?? null : null))
const openDiffLines = computed(() => (openDiff.value ? parseDiffLines(openDiff.value.body) : []))
// 差异 is the default face of a changed file — reviewing is what this tab is
// for — but a file with no diff has only one face, so the toggle is not offered.
const effectiveView = computed<FileView>(() => (openDiff.value ? fileView.value : 'edit'))

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'])
function isImagePath(path: string): boolean {
  return IMAGE_EXT.has(path.split('.').pop()?.toLowerCase() ?? '')
}
const openIsImage = computed(() => !!openPath.value && isImagePath(openPath.value))
// Raw bytes of the open file: what <img> renders for an image, and what the
// download button hands over for anything else that can't be shown as text.
const openRawUrl = computed(() =>
  openPath.value && props.projectId
    ? workspaceFileRawUrl(props.projectId, openPath.value, props.topicId ?? undefined)
    : ''
)

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// 文件 state is per-topic. openPath/fileDraft describe a file in the CURRENT
// topic's worktree, so a topic switch must drop them: carrying them over meant
// the next 保存 wrote topic A's draft into topic B's tree, at A's path.
function resetFilePanel() {
  files.value = []
  openPath.value = null
  fileDraft.value = ''
  fileSaved.value = ''
  fileVersion.value = null
  fileBinary.value = false
  fileTooLarge.value = false
  fileBytes.value = 0
  fileConflict.value = false
  expandedDirs.value = new Set()
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
  const pid = props.projectId
  if (!tid || !pid) return
  loading.value = true
  errorMsg.value = null
  try {
    const listed = (await listFiles(pid, tid)).data
    // Guard against a topic switch mid-flight — without it the previous topic's
    // listing repopulates the new panel.
    if (props.topicId !== tid) return
    files.value = listed
    const want = pendingOpen
    pendingOpen = null
    if (want) {
      await selectFile(want)
      return
    }
    // Keep the open file if it still exists; otherwise open the first one in
    // scope — which is the first CHANGED file by default, i.e. the top of the
    // review list rather than whatever sorts first in the repo.
    if (!openPath.value || !treeFiles.value.some((f) => f.path === openPath.value)) {
      openPath.value = null
      const first = treeFiles.value[0]?.path
      if (first) await selectFile(first)
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topicId === tid) loading.value = false
  }
}

async function selectFile(path: string) {
  const pid = props.projectId
  const tid = props.topicId
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
    const f = await readFile(pid, path, tid ?? undefined)
    // A topic switch mid-flight must not land the previous topic's file — and
    // its draft — in the new topic's panel.
    if (props.topicId !== tid) return
    openPath.value = path
    // Binary and oversized files arrive with no content: they open read-only,
    // so the draft stays empty and there is nothing to write back.
    fileDraft.value = f.content ?? ''
    fileSaved.value = f.content ?? ''
    fileVersion.value = f.version
    fileBinary.value = f.binary
    fileTooLarge.value = f.too_large
    fileBytes.value = f.bytes ?? listed
    revealInTree(path)
  } catch (e) {
    if (props.topicId !== tid) return
    errorMsg.value = e instanceof Error ? e.message : '读取文件失败'
  }
}

// One write path. `expected` is the version this save is based on; null means
// the human explicitly chose to overwrite after being shown the conflict.
async function writeOpenFile(expected: string | null) {
  const pid = props.projectId
  const tid = props.topicId
  const path = openPath.value
  if (!pid || !path || fileReadOnly.value || !fileDirty.value || fileSaving.value) return
  const draft = fileDraft.value
  fileSaving.value = true
  errorMsg.value = null
  try {
    const res = await writeFile(pid, path, draft, tid ?? undefined, expected)
    // The answer is only about the file that was open in the topic that was
    // open — anything else finished after a switch and must be dropped.
    if (props.topicId !== tid || openPath.value !== path) return
    fileSaved.value = draft
    fileVersion.value = res.version
    fileConflict.value = false
  } catch (e) {
    if (props.topicId !== tid || openPath.value !== path) return
    if (e instanceof ApiError && e.status === 409) {
      // 芝士 wrote this file since it was read. Neither side wins by default:
      // show the conflict and let the human reload or overwrite on purpose.
      fileConflict.value = true
    } else {
      errorMsg.value = e instanceof Error ? e.message : '保存失败'
    }
  } finally {
    if (props.topicId === tid) fileSaving.value = false
  }
}

function saveFile() {
  void writeOpenFile(fileVersion.value)
}

// 冲突后的两条出路,都由人点：丢掉自己的改动看最新的，或者明知有冲突仍然覆盖。
function overwriteFile() {
  void writeOpenFile(null)
}

function reloadOpenFile() {
  const path = openPath.value
  if (path) void selectFile(path)
}

// ---- Loading policy: the surface loads when it comes on screen, the same rule
// the drawer used ("opening the tool loads it"). One surface now, so both halves
// load together — the tree cannot mark what the diff has not told it yet. ----
function loadAll(opts: { silent?: boolean } = {}) {
  void loadGit(opts)
  void loadFiles()
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
    if (props.active) loadAll({ silent: true })
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

// Topic switch: everything here describes the previous topic's worktree.
watch(
  () => props.topicId,
  () => {
    gitCommits.value = []
    gitDiff.value = ''
    errorMsg.value = null
    resetFilePanel()
    showAll.value = false
    if (props.active) loadAll()
  }
)

// Widening (or narrowing) the scope with nothing open should land on the first
// thing in the new scope — otherwise switching to 全部文件 on a topic with no
// changes shows a tree and an empty right half.
// Land on the first file in scope whenever the scope gains one and nothing is
// open. It has to be the scope rather than the listing: the diff and the file
// list are two requests fired together, and when the listing wins the race the
// changed-files scope is still empty, so the panel would sit on an empty right
// half until the reader clicked something.
//
// Never over a directed open: `openFile` widens the scope on its way to a
// specific file, and selecting here would steal the one that was asked for —
// the same race the in-flight guard on the listing exists for.
watch(treeFiles, (rows) => {
  if (openPath.value || pendingOpen || !rows.length) return
  void selectFile(rows[0].path)
})

// A <&path> chip (chat or doc) opens that file here. WorkPanel switches to this
// tab first, then calls in.
async function openFile(path: string) {
  pendingOpen = path
  // A file reached by a <&path> chip may be one this topic never touched, and
  // then it is not in the default scope — widen so the tree can show it.
  if (!diffByPath.value.has(path)) showAll.value = true
  await loadFiles()
  // The listing may already have been in flight when this call arrived, past
  // the point where it consumes a directed open — so claim it here rather than
  // relying on which of the two got there first.
  if (pendingOpen === path) {
    pendingOpen = null
    await selectFile(path)
  }
}

defineExpose({ openFile })
</script>

<template>
  <div class="panel-changes">
    <div class="changes-bar">
      <!-- 树的范围。默认只列这个话题改过的文件 —— 验收要看的就是这些；全部文件
           是为了顺手看一眼旁边那个没动过的文件。 -->
      <div class="seg">
        <button type="button" class="seg__btn" :class="{ 'seg__btn--on': !showAll }" @click="showAll = false">
          改动
          <span v-if="fileDiffs.length" class="seg__count">{{ fileDiffs.length }}</span>
        </button>
        <button type="button" class="seg__btn" :class="{ 'seg__btn--on': showAll }" @click="showAll = true">
          全部文件
        </button>
      </div>
      <v-spacer />
      <v-btn
        icon="mdi-refresh"
        size="small"
        variant="text"
        class="c-muted"
        title="刷新"
        :loading="refreshing"
        @click="loadAll({ silent: true })"
      />
    </div>

    <!-- 转圈，不是骨架：这块地方长出来的是一套工具（150px 文件树 + 右边一格），
         而右边那一格可能是差异、编辑器、一张图，也可能是「只读 / 二进制」提示——
         等的是什么形状，这里并不知道。判据同 PanelPreview。 -->
    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">
      {{ errorMsg }}
    </v-alert>

    <div v-else class="file-tool">
      <div class="file-bar">
        <v-btn
          icon
          size="x-small"
          variant="text"
          class="file-icon-btn"
          :class="{ 'file-icon-btn--on': fileListOpen }"
          title="文件列表"
          @click="fileListOpen = !fileListOpen"
        >
          <v-icon size="18">mdi-format-list-bulleted</v-icon>
        </v-btn>
        <span class="file-bar__path" :title="openPath || ''">
          {{ openPath || '未打开文件' }}
        </span>
        <span v-if="fileDirty" class="file-bar__dot" title="未保存" />
        <v-spacer />
        <v-btn
          v-if="openPath"
          size="x-small"
          variant="text"
          prepend-icon="mdi-download-outline"
          @click="downloadOpenFile"
        >
          下载
        </v-btn>
        <!-- 看 diff / 改文件是同一个文件的两面，只有改过的文件才有两面。 -->
        <div v-if="openDiff" class="seg seg--sm">
          <button
            type="button"
            class="seg__btn"
            :class="{ 'seg__btn--on': effectiveView === 'diff' }"
            @click="fileView = 'diff'"
          >
            差异
          </button>
          <button
            type="button"
            class="seg__btn"
            :class="{ 'seg__btn--on': effectiveView === 'edit' }"
            @click="fileView = 'edit'"
          >
            编辑
          </button>
        </div>
        <!-- Read-only files (binary / oversized / images) get no 保存 button at
             all: saving one is what corrupted them. -->
        <span v-if="fileReadOnly && openPath" class="file-bar__ro">只读</span>
        <v-btn
          v-else-if="effectiveView === 'edit'"
          size="x-small"
          variant="flat"
          color="primary"
          :loading="fileSaving"
          :disabled="!fileDirty"
          @click="saveFile"
        >
          保存
        </v-btn>
      </div>
      <!-- 保存冲突: 芝士 wrote this file after it was read. Show it and let the
           human choose — a silent winner is how edits vanished. -->
      <div v-if="fileConflict" class="file-conflict">
        <v-icon size="15" class="me-1">mdi-alert-outline</v-icon>
        <span class="file-conflict__text"> 这个文件在你编辑期间被改过，多半是芝士写的。直接保存会覆盖那些改动。 </span>
        <v-btn size="x-small" variant="text" @click="reloadOpenFile">放弃我的改动，载入最新版本</v-btn>
        <v-btn size="x-small" variant="text" color="error" :loading="fileSaving" @click="overwriteFile">
          仍然覆盖保存
        </v-btn>
      </div>
      <div class="file-body">
        <div v-if="fileListOpen" ref="fileListEl" class="file-list">
          <div v-if="fileRows.length === 0" class="text-center c-faint py-6" style="font-size: 0.8rem">
            {{ showAll ? '暂无文件' : '暂无改动，采纳后会并入主干' }}
          </div>
          <template v-for="row in fileRows" :key="`${row.type}:${row.path}`">
            <!-- folder row: click toggles expand/collapse -->
            <button
              v-if="row.type === 'dir'"
              type="button"
              class="file-item file-item--dir"
              :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
              :title="row.path"
              @click="toggleDir(row.path)"
            >
              <v-icon size="13" class="c-muted">
                {{ !showAll || expandedDirs.has(row.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <v-icon size="13" class="me-1 c-muted">
                {{ !showAll || expandedDirs.has(row.path) ? 'mdi-folder-open-outline' : 'mdi-folder-outline' }}
              </v-icon>
              <span class="file-item__name">{{ row.name }}</span>
            </button>
            <!-- file row: name, and how much this topic changed in it -->
            <button
              v-else
              type="button"
              class="file-item"
              :class="{ 'file-item--active': openPath === row.path }"
              :style="{ paddingLeft: `${8 + row.depth * 14 + 13}px` }"
              :title="`${row.path} · ${fmtBytes(row.bytes)}`"
              @click="selectFile(row.path)"
            >
              <v-icon size="13" class="me-1 c-muted">mdi-file-outline</v-icon>
              <span class="file-item__name">{{ row.name }}</span>
              <!-- 变更标记: 新增 / 删除 说的是这个文件本身的去留，改过的给增删行数。 -->
              <span v-if="row.diff?.status === 'added'" class="file-mark file-mark--add">新增</span>
              <span v-else-if="row.diff?.status === 'removed'" class="file-mark file-mark--del">删除</span>
              <template v-else-if="row.diff">
                <span v-if="row.diff.added" class="file-mark file-mark--add">+{{ row.diff.added }}</span>
                <span v-if="row.diff.removed" class="file-mark file-mark--del">−{{ row.diff.removed }}</span>
              </template>
            </button>
          </template>
        </div>
        <div class="file-editor">
          <!-- 逐文件 diff: 一个文件一段，增删各自着色。整块裸 diff 读不动，也没法
               定位到文件，所以验收动线以前根本立不起来。 -->
          <div v-if="openPath && effectiveView === 'diff'" class="diff-view">
            <div v-for="(l, i) in openDiffLines" :key="i" class="diff-line" :class="`diff-line--${l.kind}`">
              {{ l.text }}
            </div>
          </div>
          <div v-else-if="openPath && openIsImage" class="file-image-view">
            <img :src="openRawUrl" :alt="openPath" />
          </div>
          <!-- Binary / oversized: no editor. Opening one in Monaco meant every
               byte utf-8 could not decode came back as U+FFFD, and 保存 wrote
               the damage to disk. -->
          <div v-else-if="openPath && fileReadOnly" class="file-blob">
            <v-icon size="30" class="c-faint mb-2">
              {{ fileTooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}
            </v-icon>
            <div class="file-blob__title">
              {{ fileTooLarge ? '文件太大，不在浏览器里打开' : '二进制文件，不能按文本编辑' }}
            </div>
            <div class="file-blob__note">
              {{ openPath }} · {{ fmtBytes(fileBytes) }}
              <template v-if="!fileTooLarge"> —— 按文本打开会损坏它，因此这里只读 </template>
            </div>
            <v-btn size="small" variant="tonal" class="mt-3" @click="downloadOpenFile">
              <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
              下载原文件
            </v-btn>
          </div>
          <!-- 手机上只读：软键盘配 Monaco 不是能救的组合，给一个明确的说法比给一个
               难用的编辑器好。 -->
          <CodeEditor
            v-else-if="openPath"
            v-model="fileDraft"
            :filename="openPath"
            :readonly="!mdAndUp"
            @save="saveFile"
          />
          <!-- 没打开文件时这一半装的是「这个话题干了什么」——提交本身是过程记录，
               它配一个位置，但不配一个和文件并列的入口。 -->
          <div v-else class="changes-scroll">
            <div class="pa-3">
              <div class="t-eyebrow mb-2">本话题提交</div>
              <div v-if="gitCommits.length === 0" class="text-medium-emphasis text-body-2">
                暂无提交，采纳后会并入主干
              </div>
              <v-list v-else density="compact" class="py-0">
                <v-list-item v-for="c in gitCommits" :key="c.hash" class="px-0">
                  <template #prepend>
                    <v-icon size="14" class="me-1 c-faint">mdi-source-commit</v-icon>
                  </template>
                  <v-list-item-title class="text-body-2">
                    {{ c.message }}
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-caption">
                    {{ c.hash.slice(0, 7) }} · {{ c.author }}
                  </v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-changes {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.changes-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
/* 分段开关 —— 下一张卡合并 Git 与 文件 时整块删掉。
   选中态靠「浮起来的一面」（surface 底 + 1px 描边 + ink 字重）而不是靠两档灰的
   明暗差：--fill 和 --surface 的明暗次序在两个主题之间是反的（浅色 surface #fff
   亮于 fill #f4f5f7，深色 surface #1b1d20 反而暗于 fill #212429），只差 6 级，
   深色下几乎看不出来 —— #526 修的侧栏选中态就是栽在这一条上。 */
.seg {
  display: inline-flex;
  gap: 2px;
}
.seg__btn {
  padding: 2px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.seg__btn:hover {
  color: var(--ink);
}
.seg__btn--on {
  background: var(--surface);
  border-color: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 范围切换上的计数：改动的文件有几个。 */
.seg__count {
  margin-left: 5px;
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}
.seg__btn--on .seg__count {
  color: var(--muted);
}
/* 没打开文件时右半边装的提交列表，也是这个 tab 唯一的另一个滚动层。 */
.changes-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}

/* 树上的变更标记。增删各自用 wash 底 + ink 字：mark 色（--ok / --danger）当文字
   在浅色主题下读不到 4.5:1，而这两个数字是要被读的，不是被瞥见的。 */
.file-mark {
  flex: 0 0 auto;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.file-mark--add {
  color: var(--ok-ink);
  background: var(--ok-wash);
}
.file-mark--del {
  color: var(--danger-ink);
  background: var(--danger-wash);
}

/* 逐文件 diff。一行一个 div 而不是一整块 <pre>：每一行要自己带底色，而增删两色
   正是「读得动」和「读不动」的全部差别。 */
.diff-view {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 6px 0;
  background: var(--surface);
  font-family: var(--font-mono);
  font-size: 0.78rem;
  line-height: 1.55;
}
.diff-line {
  padding: 0 12px;
  white-space: pre;
  color: var(--text);
}
.diff-line--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.diff-line--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.diff-line--hunk {
  margin-top: 4px;
  background: var(--fill);
  color: var(--muted);
}
.diff-line--meta {
  color: var(--faint);
}

/* 文件: a two-pane browser — list + Monaco editor. Light, to match the app.
   Fills the tab height so the editor scrolls internally. */
.file-tool {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.file-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-bottom: 1px solid rgba(var(--v-border-color), 0.5);
  flex: 0 0 auto;
}
.file-bar__path {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 52%;
}
.file-bar__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex: 0 0 auto;
}
.file-bar__ro {
  font-size: 0.72rem;
  color: var(--muted);
  border: 1px solid rgba(var(--v-border-color), 0.6);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  flex: 0 0 auto;
}
.file-conflict {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 6px 8px;
  font-size: 0.76rem;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.07);
  border-bottom: 1px solid rgba(var(--v-theme-error), 0.25);
  flex: 0 0 auto;
}
.file-conflict__text {
  flex: 1 1 200px;
  min-width: 0;
}
.file-blob {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 0.85rem;
  color: var(--text);
}
.file-blob__note {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 4px;
  word-break: break-all;
}
.file-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.file-list {
  flex: 0 0 150px;
  overflow-y: auto;
  background: var(--fill);
  border-right: 1px solid rgba(var(--v-border-color), 0.5);
  padding: 4px 0;
}
.file-item {
  display: flex;
  align-items: center;
  width: 100%;
  text-align: left;
  padding: 3px 8px 3px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text);
}
.file-item--dir .file-item__name {
  font-weight: 500;
}
.file-item:hover {
  background: rgba(var(--v-border-color), 0.18);
}
.file-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.file-item__name {
  font-family: var(--font-mono);
  font-size: 0.74rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-editor {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
}
.file-icon-btn--on :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}
.file-item--active :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}
.file-image-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow: auto;
  background: conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0) 0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: var(--shadow-1);
  /* The container's checkerboard is what says "transparent"; the image itself
     sits on the panel surface so a PNG with alpha is not slammed onto a white
     slab in the dark theme (GitHub's image viewer does the same). */
  background: var(--surface);
}
</style>
