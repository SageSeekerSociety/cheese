<script setup lang="ts">
// 改动 tab.
//
// ⚠️ 本轮它只是一个**容器**：把原来的 Git 抽屉（提交 + 裸 diff）和原来的 文件
// 抽屉（一棵不知道哪些文件被改过的树）原样搬进来，用一个分段开关并排放着，
// 行为一个字没改。
//
// 真正的合并是下一张卡的活：一棵带变更标记的文件树 + 逐文件 diff（带语法着色、
// 能按文件跳）。到那时这个分段开关连同下面两半各自的加载逻辑一起消失，
// `segment` 这个 ref 就是它留下的接缝。别在这里加功能。
import type { GitCommit, WorkspaceFile } from '../../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { ApiError, getGitDiff, getGitLog, listFiles, readFile, workspaceFileRawUrl, writeFile } from '../../api'
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

// 分段: 'git' = 本话题的提交与 diff, 'files' = 工作区文件浏览器。
// 下一张卡会把两者合成一个视图，届时这个 ref 消失。
type Segment = 'git' | 'files'
const segment = ref<Segment>('git')

const loading = ref(false)
// A background re-fetch: spins only the 刷新 button, never replaces the panel.
const refreshing = ref(false)
const errorMsg = ref<string | null>(null)

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

// ---- 文件: a two-pane browser — the file list stays visible on the left, the
// opened file loads into a code editor on the right (editable; save = 人改文件
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
// render rows — only expanded folders contribute their subtrees. Default is
// fully COLLAPSED: open exactly what you need.
interface FileRow {
  type: 'dir' | 'file'
  path: string // full relative path (dir or file)
  name: string // last segment, what we display
  depth: number
  bytes: number
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
const fileRows = computed<FileRow[]>(() => {
  interface DirNode {
    dirs: Map<string, DirNode>
    files: WorkspaceFile[]
  }
  const root: DirNode = { dirs: new Map(), files: [] }
  for (const f of files.value) {
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
      if (expandedDirs.value.has(path)) {
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
      })
    }
  }
  walk(root, '', 0)
  return rows
})

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
// directly, and flipping `segment` makes the activation watcher ask too. Letting
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
    // Keep the open file if it still exists; otherwise open the first file.
    if (!openPath.value || !files.value.some((f) => f.path === openPath.value)) {
      openPath.value = null
      if (files.value.length) await selectFile(files.value[0].path)
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

// ---- Loading policy: a segment loads when it comes on screen, the same rule
// the drawer used ("opening the tool loads it"). ----
function loadSegment(opts: { silent?: boolean } = {}) {
  if (segment.value === 'git' && !pendingOpen) void loadGit(opts)
  else void loadFiles()
}

watch(
  [() => props.active, segment],
  ([on]) => {
    if (on) loadSegment()
  },
  { immediate: true }
)

// A turn ended: 芝士's commits and its working tree just changed. Only the Git
// half was ever auto-refreshed (the file list was not), so that is what this
// keeps doing.
watch(
  () => props.refreshTick,
  () => {
    if (props.active && segment.value === 'git') void loadGit({ silent: true })
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
  [() => props.active, segment],
  ([on, seg]) => {
    stopAutoRefresh()
    if (!on || seg !== 'git') return
    refreshTimer = setInterval(() => {
      // A hidden tab polling forever is pure waste — it re-fetches on the next
      // tick after it comes back anyway.
      if (typeof document !== 'undefined' && document.hidden) return
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
    segment.value = 'git'
    if (props.active) loadSegment()
  }
)

// A <&path> chip (chat or doc) opens that file here. WorkPanel switches to this
// tab first, then calls in.
async function openFile(path: string) {
  pendingOpen = path
  segment.value = 'files'
  await loadFiles()
}

defineExpose({ openFile })
</script>

<template>
  <div class="panel-changes">
    <div class="changes-bar">
      <div class="seg">
        <!-- 分段开关：下一张卡把两半合成一个视图时，它整个消失。 -->
        <button type="button" class="seg__btn" :class="{ 'seg__btn--on': segment === 'git' }" @click="segment = 'git'">
          改动
        </button>
        <button
          type="button"
          class="seg__btn"
          :class="{ 'seg__btn--on': segment === 'files' }"
          @click="segment = 'files'"
        >
          文件
        </button>
      </div>
      <v-spacer />
      <v-btn
        v-if="segment === 'git'"
        icon="mdi-refresh"
        size="small"
        variant="text"
        class="c-muted"
        title="刷新"
        :loading="refreshing"
        @click="loadGit({ silent: true })"
      />
    </div>

    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">
      {{ errorMsg }}
    </v-alert>

    <!-- Git: this topic's own commits + the diff its 采纳 would merge. Both are
         topic-scoped; the project-level view is other topics' work and was what
         made this panel lie. -->
    <div v-else-if="segment === 'git'" class="changes-scroll">
      <div class="pa-3">
        <div class="t-eyebrow mb-2">本话题提交</div>
        <div v-if="gitCommits.length === 0" class="text-medium-emphasis text-body-2 mb-3">
          暂无提交，采纳后会并入主干
        </div>
        <v-list v-else density="compact" class="py-0 mb-3">
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

        <v-divider class="mb-3" />
        <div class="t-eyebrow mb-2">本话题改动（相对主干）</div>
        <pre v-if="gitDiff.trim()" class="code-pre">{{ gitDiff }}</pre>
        <div v-else class="text-medium-emphasis text-body-2">暂无改动</div>
      </div>
    </div>

    <!-- 文件: two-pane — the list stays on the left, the opened file loads into a
         code editor on the right (editable; 保存 = 人改文件即指令). -->
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
        <!-- Read-only files (binary / oversized / images) get no 保存 button at
             all: saving one is what corrupted them. -->
        <span v-if="fileReadOnly && openPath" class="file-bar__ro">只读</span>
        <v-btn
          v-else
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
          <div v-if="files.length === 0" class="text-center c-faint py-6" style="font-size: 0.8rem">暂无文件</div>
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
                {{ expandedDirs.has(row.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <v-icon size="13" class="me-1 c-muted">
                {{ expandedDirs.has(row.path) ? 'mdi-folder-open-outline' : 'mdi-folder-outline' }}
              </v-icon>
              <span class="file-item__name">{{ row.name }}</span>
            </button>
            <!-- file row: shows only the file name, indented under its folder -->
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
            </button>
          </template>
        </div>
        <div class="file-editor">
          <div v-if="openPath && openIsImage" class="file-image-view">
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
            <v-btn
              size="small"
              variant="tonal"
              class="mt-3"
              :href="openRawUrl || undefined"
              target="_blank"
              rel="noopener"
            >
              <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
              下载原文件
            </v-btn>
          </div>
          <CodeEditor v-else-if="openPath" v-model="fileDraft" :filename="openPath" @save="saveFile" />
          <div v-else class="d-flex align-center justify-center fill-height c-faint" style="font-size: 0.85rem">
            选择左侧文件查看或编辑
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
/* Git 半边的滚动层 —— 这个 tab 只有一个滚动条。 */
.changes-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}
.code-pre {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  line-height: 1.5;
  background: var(--fill);
  color: var(--text);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
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
