<script setup lang="ts">
// A task's「文件」panel: the task's working copy — the one its AI teammate
// works in — as a tree on the left and the files opened from it on the right,
// a tab each, one shown. Files side by side are cells of the layout: a file
// opened beside (`single`) is a cell of its own with that file, no tree and no
// tabs.
//
// It draws what `composables/useTaskFiles` holds and hands the person's
// actions back to it; nothing here fetches. On a phone the file is shown as
// text, without loading the editor, and without tabs.
import type { OpenTaskFile, TaskFilesBundle } from '../../composables/useTaskFiles'
import type { LanguageLink } from '../../lib/lsp/link'
import type { LayoutPanelWidth } from '../layout-area'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { foldersOf, taskFileRows } from '../../lib/taskFileTree'
import ConfirmDialog from '../base/ConfirmDialog.vue'
import CodeEditor from '../CodeEditor.vue'
import AdaptiveDialog from '../common/AdaptiveDialog.vue'
import FileKindMark from '../common/FileKindMark.vue'
import { rememberedWidth, rememberWidth } from '../layout-area'
import ReviewMergePicker from '../review/ReviewMergePicker.vue'

import ChangesOpenFile from './ChangesOpenFile.vue'
import TaskFileTree from './TaskFileTree.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** The panel's data and actions (`composables/useTaskFiles`). */
    files: TaskFilesBundle
    /** Files this task changed (the work summary's list). */
    changed?: string[]
    /** The person may not change this task's files. */
    readOnly?: boolean
    /** A phone: the file is shown as text and not edited. */
    phone?: boolean
    agentName?: string
    /** A cell holding one file: no tree, and no finding another file here. */
    single?: boolean
    /** The task's language servers, for the editor (`composables/useTaskLanguage`). */
    language?: LanguageLink | null
  }>(),
  { changed: () => [], readOnly: false, phone: false, agentName: '', single: false, language: null }
)

const emit = defineEmits<{
  /** Open this file in a cell of its own beside this one. */
  'open-beside': [path: string]
}>()

const f = props.files

// ---- the tree ----
const openDirs = ref<Set<string>>(new Set())
const revealTick = ref(0)
watch(
  () => f.openPath.value,
  (path) => {
    if (!path) return
    const next = new Set(openDirs.value)
    for (const dir of foldersOf(path)) next.add(dir)
    openDirs.value = next
    revealTick.value += 1
  },
  { immediate: true }
)
function collapseAll() {
  openDirs.value = new Set()
}
function toggle(path: string) {
  const next = new Set(openDirs.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  openDirs.value = next
}
const rows = computed(() => taskFileRows(f.files.value, f.dirs.value, openDirs.value))
const changedSet = computed(() => new Set([...props.changed, ...f.touched.value]))
const treeEditable = computed(() => !props.readOnly && !props.phone)

// ---- the tree's width: dragged on the line beside it, remembered in this browser ----
const TREE_WIDTH: LayoutPanelWidth = { initial: 232, min: 160, max: 420, storageKey: 'cheesex.taskFilesTreeWidth' }
const treeWidth = rememberedWidth(TREE_WIDTH)
const TREE_STEP = 16
const dragging = ref(false)
let stopDrag: (() => void) | null = null
function startTreeDrag(event: PointerEvent) {
  const handle = event.currentTarget as HTMLElement
  const body = handle.parentElement
  if (!body || event.button !== 0) return
  event.preventDefault()
  const left = body.getBoundingClientRect().left
  handle.setPointerCapture?.(event.pointerId)
  dragging.value = true
  const move = (e: PointerEvent) => rememberWidth(TREE_WIDTH, e.clientX - left, false)
  const stop = () => {
    handle.removeEventListener('pointermove', move)
    handle.removeEventListener('pointerup', stop)
    handle.removeEventListener('pointercancel', stop)
    dragging.value = false
    stopDrag = null
    rememberWidth(TREE_WIDTH, treeWidth.value)
  }
  handle.addEventListener('pointermove', move)
  handle.addEventListener('pointerup', stop)
  handle.addEventListener('pointercancel', stop)
  stopDrag = stop
}
onBeforeUnmount(() => stopDrag?.())
function onTreeHandleKey(event: KeyboardEvent) {
  if (event.key === 'ArrowLeft') rememberWidth(TREE_WIDTH, treeWidth.value - TREE_STEP)
  else if (event.key === 'ArrowRight') rememberWidth(TREE_WIDTH, treeWidth.value + TREE_STEP)
  else if (event.key === 'Home') rememberWidth(TREE_WIDTH, TREE_WIDTH.initial)
  else return
  event.preventDefault()
}

// ---- the tabs: one per file opened here (not in a cell holding one file, not on a phone) ----
const showTabs = computed(() => !props.single && !props.phone)
const baseName = (path: string) => path.slice(path.lastIndexOf('/') + 1)
const openFolders = computed(() => {
  const path = f.openPath.value ?? ''
  const at = path.lastIndexOf('/')
  return at < 0 ? [] : path.slice(0, at).split('/')
})
function openShownBeside() {
  const path = f.openPath.value
  if (path) emit('open-beside', path)
}

// ---- the open file ----
const editable = computed(() => !props.phone && f.canEdit.value)
const textFile = computed(() => {
  const now = f.file.value
  return now && !now.binary && !now.tooLarge ? now : null
})
// The editor stays mounted while the person moves between tabs, so each file
// keeps its undo history and where it was scrolled (`CodeEditor`'s `keep`):
// while a file not read yet loads, it holds the last text file, hidden.
const editorFile = ref<OpenTaskFile | null>(null)
watch(
  textFile,
  (now) => {
    if (now) editorFile.value = now
  },
  { immediate: true }
)
watch(
  () => f.openPath.value,
  (path) => {
    if (!path) editorFile.value = null
  }
)
function onEdit(text: string) {
  // Only the file shown takes typing; the editor holds another only while hidden.
  if (editorFile.value && editorFile.value.path === f.openPath.value) f.edit(text)
}
const status = computed(() => {
  if (!f.file.value) return ''
  if (!editable.value) return t('work.room.changes.readOnly')
  if (f.saving.value) return t('global.saveState.saving')
  if (f.held.value) return t('global.saveState.unsaved')
  return t('global.saveState.saved')
})

// ---- the lines a file link pointed at, on a phone: scrolled to (the editor reveals them itself) ----
const textEl = ref<HTMLElement | null>(null)
watch(
  () => [props.phone, f.lines.value, f.file.value?.path] as const,
  ([phone, at]) => {
    if (!phone || !at) return
    void nextTick(() => {
      const el = textEl.value
      if (!el) return
      const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 16
      el.scrollTop = (at.start - 1) * lineHeight
    })
  },
  { immediate: true }
)

// ---- find a file by name (Ctrl+P / ⌘P) ----
const quickOpen = ref(false)
function onKeydown(event: KeyboardEvent) {
  if (props.single) return
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'p') {
    event.preventDefault()
    quickOpen.value = true
  }
}
function pickQuick(path: string) {
  quickOpen.value = false
  void f.open(path)
}

// ---- creating, renaming, deleting ----
type Naming = { kind: 'file' | 'dir'; folder: string } | { kind: 'rename'; path: string }
const naming = ref<Naming | null>(null)
const nameDraft = ref('')
const namingOpen = ref(false)
const busy = ref(false)
function askCreate(kind: 'file' | 'dir', folder: string) {
  naming.value = { kind, folder }
  nameDraft.value = ''
  namingOpen.value = true
}
function askRename(path: string) {
  naming.value = { kind: 'rename', path }
  nameDraft.value = path.slice(path.lastIndexOf('/') + 1)
  namingOpen.value = true
}
const namingTitle = computed(() => {
  const now = naming.value
  if (!now) return ''
  if (now.kind === 'rename') return t('project.artifacts.rename')
  return now.kind === 'file' ? t('work.room.taskFiles.newFile') : t('work.room.taskFiles.newFolder')
})
const cleanName = computed(() => nameDraft.value.trim().replace(/^\/+|\/+$/g, ''))
async function submitName() {
  const now = naming.value
  const name = cleanName.value
  if (!now || !name) return
  busy.value = true
  let ok: boolean
  if (now.kind === 'rename') {
    const at = now.path.lastIndexOf('/')
    const to = at < 0 ? name : `${now.path.slice(0, at)}/${name}`
    ok = to === now.path || (await f.arrange({ operation: 'move', path: now.path, to }))
  } else {
    const path = now.folder ? `${now.folder}/${name}` : name
    ok = await f.arrange({ operation: now.kind === 'file' ? 'create' : 'mkdir', path })
    if (ok && now.folder) openDirs.value = new Set(openDirs.value).add(now.folder)
  }
  busy.value = false
  if (ok) namingOpen.value = false
}

const removing = ref<string | null>(null)
const removeOpen = ref(false)
function askRemove(path: string) {
  removing.value = path
  removeOpen.value = true
}
async function confirmRemove() {
  const path = removing.value
  if (!path) return
  busy.value = true
  const ok = await f.arrange({ operation: 'delete', path })
  busy.value = false
  if (ok) removeOpen.value = false
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- listens for Ctrl+P inside the panel; the tree and the editor are the controls -->
  <div class="task-files" @keydown="onKeydown">
    <div v-if="f.machine.value === 'preparing'" class="task-files__strip" role="status">
      <v-progress-circular indeterminate size="14" width="2" class="task-files__spin" />
      <span class="task-files__strip-text">{{ t('work.room.taskFiles.preparing') }}</span>
    </div>
    <div
      v-else-if="f.machine.value === 'failed' || f.saveError.value"
      class="task-files__strip task-files__strip--error"
      role="alert"
    >
      <span class="task-files__strip-text">{{ f.saveError.value || t('apiError.taskMachinePrepareFailed') }}</span>
      <BaseButton kind="ghost" size="sm" @click="f.retry()">{{ t('global.loadError.retry') }}</BaseButton>
    </div>
    <div v-if="f.arrangeError.value" class="task-files__strip task-files__strip--error" role="alert">
      <span class="task-files__strip-text">{{ f.arrangeError.value }}</span>
    </div>
    <ReviewMergePicker
      v-if="f.merge.value"
      :regions="f.merge.value.regions"
      :agent-name="props.agentName"
      :busy="f.saving.value"
      @resolve="f.resolveMerge($event)"
      @cancel="f.cancelMerge()"
    />
    <div v-else-if="f.conflict.value" class="task-files__strip task-files__strip--error" role="alert">
      <span class="task-files__strip-text">{{ t('work.room.changes.conflict') }}</span>
      <BaseButton kind="ghost" size="sm" @click="f.reloadLatest()">{{
        t('work.room.changes.reloadLatest')
      }}</BaseButton>
      <BaseButton kind="danger" size="sm" :loading="f.saving.value" @click="f.keepMine()">
        {{ t('work.room.changes.saveAnyway') }}
      </BaseButton>
    </div>

    <div
      class="task-files__body"
      :class="{ 'task-files__body--phone': props.phone, 'task-files__body--single': props.single }"
      :style="{ '--task-files-tree': `${treeWidth}px` }"
    >
      <TaskFileTree
        v-if="!props.single"
        :rows="rows"
        :open="openDirs"
        :active-path="f.openPath.value"
        :changed="changedSet"
        :editable="treeEditable"
        :reveal-tick="revealTick"
        :can-split="!props.phone"
        @select="f.open($event)"
        @open-beside="emit('open-beside', $event)"
        @toggle="toggle"
        @create="askCreate"
        @rename="askRename"
        @remove="askRemove"
        @find="quickOpen = true"
        @collapse-all="collapseAll"
      />
      <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a focusable separator: pointer drag, arrow keys, double-click reset -->
      <div
        v-if="!props.single && !props.phone"
        class="task-files__sash"
        :class="{ 'task-files__sash--drag': dragging }"
        role="separator"
        aria-orientation="vertical"
        tabindex="0"
        :aria-valuenow="treeWidth"
        :aria-valuemin="TREE_WIDTH.min"
        :aria-valuemax="TREE_WIDTH.max"
        :aria-label="t('work.room.taskFiles.treeWidth')"
        :title="t('work.topic.resize')"
        @pointerdown="startTreeDrag"
        @keydown="onTreeHandleKey"
        @dblclick="rememberWidth(TREE_WIDTH, TREE_WIDTH.initial)"
      />
      <div class="task-files__file">
        <div v-if="f.listing.value && !f.files.value.length" class="task-files__center">
          <v-progress-circular indeterminate color="primary" size="24" />
        </div>
        <p v-else-if="f.listError.value" class="task-files__note">{{ f.listError.value }}</p>
        <template v-else-if="f.openPath.value">
          <div v-if="showTabs" class="task-files__tabs">
            <div class="task-files__tablist" :aria-label="t('work.room.taskFiles.openFiles')" role="group">
              <div
                v-for="path in f.tabs.value"
                :key="path"
                class="file-tab"
                :class="{
                  'file-tab--active': path === f.openPath.value,
                  'file-tab--held': f.heldTabs.value.has(path),
                }"
              >
                <button
                  type="button"
                  class="file-tab__main"
                  :title="path"
                  :aria-current="path === f.openPath.value ? 'true' : undefined"
                  @click="f.open(path)"
                >
                  <FileKindMark :name="path" />
                  <span class="file-tab__name">{{ baseName(path) }}</span>
                </button>
                <button
                  type="button"
                  class="file-tab__close"
                  :aria-label="t('work.room.taskFiles.closeTab', { name: baseName(path) })"
                  :title="t('work.room.taskFiles.closeTab', { name: baseName(path) })"
                  @click="f.closeTab(path)"
                >
                  <span class="file-tab__dot" />
                  <v-icon size="14" class="file-tab__x">mdi-close</v-icon>
                </button>
              </div>
            </div>
            <div class="task-files__tabs-end">
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-dock-right"
                :aria-label="t('work.room.taskFiles.openBeside')"
                :title="t('work.room.taskFiles.openBeside')"
                @click="openShownBeside"
              />
            </div>
          </div>
          <div class="task-files__head">
            <span class="task-files__path" :title="f.openPath.value">
              <span v-for="(folder, i) in openFolders" :key="i">{{ folder }} / </span>
              <span class="task-files__name">{{ baseName(f.openPath.value ?? '') }}</span>
            </span>
            <span class="task-files__status">{{ status }}</span>
          </div>
          <p v-if="f.fileError.value" class="task-files__note">{{ f.fileError.value }}</p>
          <div v-else-if="f.opening.value && !f.file.value" class="task-files__center">
            <v-progress-circular indeterminate color="primary" size="24" />
          </div>
          <template v-else-if="f.file.value">
            <p v-if="f.file.value.binary || f.file.value.tooLarge" class="task-files__note">
              {{ f.file.value.tooLarge ? t('work.room.changes.tooLarge') : t('work.room.changes.binary') }}
            </p>
            <pre v-else-if="props.phone" ref="textEl" class="task-files__text">{{ f.file.value.content }}</pre>
          </template>
          <CodeEditor
            v-if="!props.phone && editorFile"
            v-show="!!textFile && !f.fileError.value"
            class="task-files__editor"
            :model-value="editorFile.content"
            :filename="editorFile.path"
            :readonly="!editable"
            :lines="editorFile.path === f.openPath.value ? f.lines.value : null"
            :language="props.language"
            :keep="showTabs ? f.tabs.value : []"
            @update:model-value="onEdit"
            @save="f.saveNow()"
            @quick-open="quickOpen = !props.single"
          />
        </template>
        <p v-else-if="showTabs && f.listed.value" class="task-files__empty">
          {{ t('work.room.taskFiles.noOpenFile') }}
        </p>
      </div>
    </div>

    <ChangesOpenFile v-if="!props.single" v-model="quickOpen" :files="f.files.value" @pick="pickQuick" />
    <AdaptiveDialog
      v-model="namingOpen"
      :title="namingTitle"
      size="sm"
      :primary-label="namingTitle"
      :primary-loading="busy"
      :primary-disabled="!cleanName"
      @primary="submitName"
    >
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the person asked to name something; the field takes the focus -->
      <v-text-field
        v-model="nameDraft"
        autofocus
        autocomplete="off"
        variant="outlined"
        density="compact"
        hide-details
        :label="t('work.room.taskFiles.name')"
        @keydown.enter.prevent="submitName"
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
    </AdaptiveDialog>
    <ConfirmDialog
      v-model="removeOpen"
      :title="t('work.room.taskFiles.deleteTitle', { name: removing ?? '' })"
      :confirm-label="t('project.artifacts.delete')"
      danger
      :loading="busy"
      @confirm="confirmRemove"
    />
  </div>
</template>

<style scoped>
.task-files {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.task-files__strip {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
  background: var(--fill);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.task-files__strip--error {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.task-files__strip-text {
  flex: 1 1 auto;
  min-width: 0;
}
.task-files__spin {
  flex: none;
  color: var(--muted);
}
@media (prefers-reduced-motion: reduce) {
  .task-files__spin :deep(.v-progress-circular__overlay),
  .task-files__spin :deep(svg) {
    animation: none;
  }
}
.task-files__body {
  display: grid;
  grid-template-columns: var(--task-files-tree) 5px minmax(0, 1fr);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.task-files__body--single {
  grid-template-columns: minmax(0, 1fr);
}
.task-files__body--phone {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 40%) minmax(0, 1fr);
}
.task-files__body--phone > .task-tree {
  border-bottom: 1px solid var(--line);
}
/* The line between the tree and the files, with a grip that shows at rest and
   darkens on hover and while dragged. */
.task-files__sash {
  position: relative;
  border-left: 1px solid var(--line);
  cursor: col-resize;
  touch-action: none;
}
.task-files__sash::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 1px;
  width: 2px;
  height: 28px;
  margin-top: -14px;
  border-radius: var(--radius-pill);
  background: var(--faint);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-files__sash:hover::after,
.task-files__sash:focus-visible::after,
.task-files__sash--drag::after {
  background: var(--muted);
}
.task-files__sash:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.task-files__tabs {
  display: flex;
  flex: none;
  align-items: stretch;
  height: 34px;
  min-width: 0;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}
.task-files__tablist {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.task-files__tabs-end {
  display: flex;
  flex: none;
  align-items: center;
  padding: 0 4px;
  border-left: 1px solid var(--line);
}
.file-tab {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  max-width: 200px;
  padding-right: 6px;
  border-right: 1px solid var(--line);
  color: var(--muted);
}
.file-tab:hover {
  background: var(--fill);
  color: var(--text);
}
.file-tab--active,
.file-tab--active:hover {
  background: var(--surface);
  color: var(--ink);
}
/* The tab shown sits open onto the file below it, with a bar on top. */
.file-tab--active::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 2px;
  background: var(--ink);
}
.file-tab--active::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 1px;
  background: var(--surface);
}
.file-tab__main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  height: 100%;
  padding: 0 6px 0 10px;
  border: none;
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.file-tab__main:focus-visible,
.file-tab__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.file-tab__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-tab__close {
  display: grid;
  flex: none;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}
.file-tab__close:hover {
  background: var(--line-2);
  color: var(--ink);
}
/* A tab with text not saved yet shows a dot where the × is; pointing at it shows the ×. */
.file-tab__dot {
  display: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--muted);
}
.file-tab--held .file-tab__dot {
  display: block;
}
.file-tab--held .file-tab__x,
.file-tab--held .file-tab__close:hover .file-tab__dot,
.file-tab--held .file-tab__close:focus-visible .file-tab__dot {
  display: none;
}
.file-tab--held .file-tab__close:hover .file-tab__x,
.file-tab--held .file-tab__close:focus-visible .file-tab__x {
  display: inline-flex;
}
/* A tab neither shown, pointed at nor holding text keeps its × out of sight. */
.file-tab:not(.file-tab--active, .file-tab--held, :hover, :focus-within) .file-tab__close {
  opacity: 0;
}
@media (hover: none) {
  .file-tab .file-tab__close {
    opacity: 1;
  }
}
.task-files__file {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.task-files__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 12px;
  min-width: 0;
}
.task-files__body--single .task-files__head,
.task-files__body--phone .task-files__head {
  height: 34px;
  border-bottom: 1px solid var(--line);
}
.task-files__path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-files__name {
  color: var(--text);
}
.task-files__status {
  flex: none;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}
.task-files__editor {
  flex: 1 1 auto;
  min-height: 0;
}
.task-files__text {
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  padding: 8px 12px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--text);
  white-space: pre;
}
.task-files__center {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
}
.task-files__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.task-files__note {
  margin: 0;
  padding: 16px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
