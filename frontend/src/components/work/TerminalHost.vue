<script setup lang="ts">
// One 终端 tab: the person's shell `terminalId` in the task, drawn by `PanelTerminal`.
import type { TaskTerminalReason } from '../../composables/useTaskTerminal'

import { computed, onMounted, ref } from 'vue'

import { useTaskTerminal } from '../../composables/useTaskTerminal'
import { t } from '../../i18n'
import PanelTerminal from '../panels/PanelTerminal.vue'

const props = defineProps<{ taskId: string; terminalId: string; active: boolean }>()

const screen = ref<InstanceType<typeof PanelTerminal> | null>(null)

const { state, reason, start, type, resize } = useTaskTerminal(
  () => props.taskId,
  () => props.terminalId,
  {
    output: (bytes, done) => (screen.value ? screen.value.write(bytes, done) : done()),
    reset: () => screen.value?.reset(),
    size: () => screen.value?.size() ?? { cols: 80, rows: 24 },
  }
)

const REFUSALS = {
  not_owner: 'work.room.terminal.notOwner',
  unsupported: 'work.room.terminal.unsupported',
  outdated: 'work.room.terminal.outdated',
  limit: 'work.room.terminal.limit',
} as const satisfies Record<TaskTerminalReason, string>

const placeholder = computed(() => {
  if (state.value === 'preparing') return t('work.room.taskFiles.preparing')
  if (state.value === 'cold') return t('work.room.terminal.empty')
  if (state.value === 'failed') return t('work.room.terminal.cannotConnect')
  if (state.value === 'refused' && reason.value) return t(REFUSALS[reason.value])
  return null
})
const note = computed(() => (state.value === 'ended' ? t('work.room.terminal.ended') : null))

onMounted(() => void start())
</script>

<template>
  <PanelTerminal
    ref="screen"
    :placeholder="placeholder"
    :note="note"
    :active="active"
    @type="type"
    @resize="resize()"
    @click="state === 'cold' && start()"
  />
</template>
