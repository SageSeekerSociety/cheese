<script setup lang="ts">
// One 终端 tab: the person's shell `terminalId` in the task, drawn by `PanelTerminal`.
//
// While there is no shell to show, the screen says why, never nothing: the task's environment being
// prepared, or the shell being reached (which wakes a frozen environment, and can take a while),
// is said as every surface of the task page says it (`TaskEnvironmentNote`), with how long it has
// waited; a failure says what failed with 「重试」; a shell whose environment is gone offers to open
// a new one.
import type { TaskTerminalReason } from '../../composables/useTaskTerminal'

import { computed, onMounted, ref } from 'vue'

import { useTaskTerminal } from '../../composables/useTaskTerminal'
import { t } from '../../i18n'
import BaseButton from '../base/BaseButton.vue'
import TaskEnvironmentNote from '../common/TaskEnvironmentNote.vue'
import PanelTerminal from '../panels/PanelTerminal.vue'

const props = defineProps<{ taskId: string; terminalId: string; active: boolean }>()
const emit = defineEmits<{ named: [name: { number?: number; program: string | null }] }>()

const screen = ref<InstanceType<typeof PanelTerminal> | null>(null)

const { state, reason, connectingSince, environment, start, retry, type, resize } = useTaskTerminal(
  () => props.taskId,
  () => props.terminalId,
  {
    output: (bytes, done) => (screen.value ? screen.value.write(bytes, done) : done()),
    reset: () => screen.value?.reset(),
    size: () => screen.value?.size() ?? { cols: 80, rows: 24 },
    named: (name) => emit('named', name),
  }
)

const REFUSALS = {
  not_owner: 'work.room.terminal.notOwner',
  unsupported: 'work.room.terminal.unsupported',
  outdated: 'work.room.terminal.outdated',
  limit: 'work.room.terminal.limit',
} as const satisfies Record<TaskTerminalReason, string>

/** What stands in for the screen while there is no shell to show. */
const cover = computed(() => {
  if (state.value === 'preparing') {
    const env = environment.state.value
    if (env === 'failed' || env === 'unanswered') {
      return { note: env, since: null, message: '', detail: environment.detail.value }
    }
    return { note: 'preparing' as const, since: environment.since.value, message: '', detail: '' }
  }
  if (state.value === 'connecting') {
    return { note: 'preparing' as const, since: connectingSince.value, message: '', detail: '' }
  }
  if (state.value === 'failed') {
    return { note: 'failed' as const, since: null, message: t('work.room.terminal.cannotConnect'), detail: '' }
  }
  return null
})
const placeholder = computed(() => (state.value === 'refused' && reason.value ? t(REFUSALS[reason.value]) : null))
const note = computed(() => (state.value === 'ended' ? t('work.room.terminal.ended') : null))

onMounted(() => void start())
</script>

<template>
  <PanelTerminal
    ref="screen"
    :placeholder="placeholder"
    :covered="!!cover || state === 'cold'"
    :note="note"
    :active="active"
    @type="type"
    @resize="resize()"
  >
    <template #cover>
      <TaskEnvironmentNote
        v-if="cover"
        centered
        :state="cover.note"
        :since="cover.since"
        :message="cover.message"
        :detail="cover.detail"
        @retry="retry()"
      />
      <div v-else-if="state === 'cold'" class="term-cold">
        <span>{{ t('work.room.terminal.empty') }}</span>
        <BaseButton kind="secondary" size="sm" @click="start()">{{ t('work.room.terminal.start') }}</BaseButton>
      </div>
    </template>
  </PanelTerminal>
</template>

<style scoped>
.term-cold {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
