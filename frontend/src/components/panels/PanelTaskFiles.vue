<script setup lang="ts">
// A task's「文件」panel: the task's working copy — the one its AI teammate
// works in — as a tree on the left and one file on the right. One file per
// panel: files side by side are cells of the layout, not tabs in here.
//
// It draws what `composables/useTaskFiles` holds and hands the person's
// actions back to it; nothing here fetches. On a phone the file is shown as
// text, without loading the editor.
import type { TaskFilesBundle } from '../../composables/useTaskFiles'

import { computed, ref, watch } from 'vue'

import { foldersOf, taskFileRows } from '../../lib/taskFileTree'
import ConfirmDialog from '../base/ConfirmDialog.vue'
import CodeEditor from '../CodeEditor.vue'
import AdaptiveDialog from '../common/AdaptiveDialog.vue'
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
  }>(),
  { changed: () => [], readOnly: false, phone: false, agentName: '' }
)

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
function toggle(path: string) {
  const next = new Set(openDirs.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  openDirs.value = next
}
const rows = computed(() => taskFileRows(f.files.value, f.dirs.value, openDirs.value))
const changedSet = computed(() => new Set([...props.changed, ...f.touched.value]))
const treeEditable = computed(() => !props.readOnly && !props.phone && f.machine.value !== 'no_session')

// ---- the open file ----
const editable = computed(() => !props.phone && f.canEdit.value)
const status = computed(() => {
  if (!f.file.value) return ''
  if (!editable.value) return t('work.room.changes.readOnly')
  if (f.saving.value) return t('global.saveState.saving')
  if (f.held.value) return t('global.saveState.unsaved')
  return t('global.saveState.saved')
})

// ---- find a file by name (Ctrl+P / ⌘P) ----
const quickOpen = ref(false)
function onKeydown(event: KeyboardEvent) {
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
    <div v-else-if="f.machine.value === 'no_session' && !props.readOnly" class="task-files__strip">
      <span class="task-files__strip-text">{{ t('apiError.taskMachineNoSession') }}</span>
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

    <div class="task-files__body" :class="{ 'task-files__body--phone': props.phone }">
      <TaskFileTree
        :rows="rows"
        :open="openDirs"
        :active-path="f.openPath.value"
        :changed="changedSet"
        :editable="treeEditable"
        :reveal-tick="revealTick"
        @select="f.open($event)"
        @toggle="toggle"
        @create="askCreate"
        @rename="askRename"
        @remove="askRemove"
      />
      <div class="task-files__file">
        <div v-if="f.listing.value && !f.files.value.length" class="task-files__center">
          <v-progress-circular indeterminate color="primary" size="24" />
        </div>
        <p v-else-if="f.listError.value" class="task-files__note">{{ f.listError.value }}</p>
        <template v-else-if="f.openPath.value">
          <div class="task-files__head">
            <span class="task-files__path" :title="f.openPath.value">{{ f.openPath.value }}</span>
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
            <pre v-else-if="props.phone" class="task-files__text">{{ f.file.value.content }}</pre>
            <CodeEditor
              v-else
              class="task-files__editor"
              :model-value="f.file.value.content"
              :filename="f.file.value.path"
              :readonly="!editable"
              @update:model-value="f.edit($event)"
              @save="f.saveNow()"
              @quick-open="quickOpen = true"
            />
          </template>
        </template>
      </div>
    </div>

    <ChangesOpenFile v-model="quickOpen" :files="f.files.value" @pick="pickQuick" />
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
  grid-template-columns: 172px minmax(0, 1fr);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.task-files__body--phone {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 40%) minmax(0, 1fr);
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
  height: 34px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--line);
  min-width: 0;
}
.task-files__path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
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
.task-files__note {
  margin: 0;
  padding: 16px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
