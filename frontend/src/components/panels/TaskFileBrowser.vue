<script setup lang="ts">
// A task's file tree with what a person does to it: open a file, open it beside,
// create, rename and delete (asking for a name or a confirmation here), find a file
// by name (`find`, the page opens its finder) and fold every folder. The same tree
// sits beside the open files in the 对话 form's 文件 panel and in a column of its own
// in the 编辑器 form; which folders are open is the page's (`useTaskFileTree`), so both
// show the same. It draws what `composables/useTaskFiles` holds and fetches nothing.
import type { TaskFilesBundle } from '../../composables/useTaskFiles'
import type { TaskFileTreeState } from '../../composables/useTaskFileTree'

import { computed, ref } from 'vue'

import { taskFileRows } from '../../lib/taskFileTree'
import ConfirmDialog from '../base/ConfirmDialog.vue'
import AdaptiveDialog from '../common/AdaptiveDialog.vue'

import TaskFileTree from './TaskFileTree.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    files: TaskFilesBundle
    tree: TaskFileTreeState
    /** Files this task changed (the work summary's list). */
    changed?: string[]
    /** The person may change the tree. */
    editable?: boolean
    /** Not a phone: the head row, and opening a file in a cell beside. */
    canSplit?: boolean
  }>(),
  { changed: () => [], editable: false, canSplit: true }
)

const emit = defineEmits<{
  select: [path: string]
  'open-beside': [path: string]
  /** Find a file by name. */
  find: []
}>()

const f = props.files
const rows = computed(() => taskFileRows(f.files.value, f.dirs.value, props.tree.open.value))
const changedSet = computed(() => new Set([...props.changed, ...f.touched.value]))

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
    if (ok && now.folder) props.tree.openFolder(now.folder)
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
  <div class="task-file-browser">
    <TaskFileTree
      class="task-file-browser__tree"
      :rows="rows"
      :open="props.tree.open.value"
      :active-path="f.openPath.value"
      :changed="changedSet"
      :editable="props.editable"
      :reveal-tick="props.tree.revealTick.value"
      :can-split="props.canSplit"
      @select="emit('select', $event)"
      @open-beside="emit('open-beside', $event)"
      @toggle="props.tree.toggle"
      @create="askCreate"
      @rename="askRename"
      @remove="askRemove"
      @find="emit('find')"
      @collapse-all="props.tree.collapseAll"
    />
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
.task-file-browser {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.task-file-browser__tree {
  flex: 1 1 auto;
}
</style>
