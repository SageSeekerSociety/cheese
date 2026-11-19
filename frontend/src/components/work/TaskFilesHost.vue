<script setup lang="ts">
// The wiring shell of a task's「文件」panel: it holds the panel's data
// (`composables/useTaskFiles`) and hands it to the scene, which only draws
// (`panels/PanelTaskFiles.vue`). It stands outside `components/panels/` for
// the reason `PanelChangesHost` gives.
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
  }>(),
  { readOnly: false, active: false, refreshTick: 0, changed: () => [], phone: false, agentName: '' }
)

const files = useTaskFiles({
  projectId: () => props.projectId,
  taskId: () => props.taskId,
  readOnly: () => props.readOnly,
  active: () => props.active,
  refreshTick: () => props.refreshTick,
})

// A file link in the conversation opens here when the task's copy has it.
defineExpose({ openFile: (path: string) => files.openFile(path) })
</script>

<template>
  <PanelTaskFiles
    :files="files"
    :changed="props.changed"
    :read-only="props.readOnly"
    :phone="props.phone"
    :agent-name="props.agentName"
  />
</template>
