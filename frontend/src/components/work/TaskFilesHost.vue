<script setup lang="ts">
// The wiring shell of a task's「文件」panel: it holds the panel's data
// (`composables/useTaskFiles`) and hands it to the scene, which only draws
// (`panels/PanelTaskFiles.vue`). It stands outside `components/panels/` for
// the reason `PanelChangesHost` gives. Given a `path`, it is a cell holding that
// one file, opened beside the 文件 panel.
import { watch } from 'vue'

import { useTaskFiles } from '../../composables/useTaskFiles'
import PanelTaskFiles from '../panels/PanelTaskFiles.vue'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    taskId: string | null
    readOnly?: boolean
    /** The panel is on screen. */
    active?: boolean
    /** Bumped when a turn ends. */
    refreshTick?: number
    /** Files this task changed. */
    changed?: string[]
    phone?: boolean
    agentName?: string
    /** The one file of a cell opened beside the 文件 panel. */
    path?: string | null
  }>(),
  { readOnly: false, active: false, refreshTick: 0, changed: () => [], phone: false, agentName: '', path: null }
)

const emit = defineEmits<{ 'open-beside': [path: string] }>()

const files = useTaskFiles({
  projectId: () => props.projectId,
  taskId: () => props.taskId,
  readOnly: () => props.readOnly,
  active: () => props.active,
  refreshTick: () => props.refreshTick,
})

watch(
  () => props.path,
  (path) => {
    if (path) void files.open(path)
  },
  { immediate: true }
)

// A file link in the conversation opens here when the task's copy has it, at
// the lines it points at.
defineExpose({
  openFile: (path: string, lines: { start: number; end: number } | null = null) => files.openFile(path, lines),
})
</script>

<template>
  <PanelTaskFiles
    :files="files"
    :changed="props.changed"
    :read-only="props.readOnly"
    :phone="props.phone"
    :agent-name="props.agentName"
    :single="!!props.path"
    @open-beside="emit('open-beside', $event)"
  />
</template>
