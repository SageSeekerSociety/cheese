<script setup lang="ts">
// 运行记录: a button in the terminal's bar that opens the commands a person can run again in their
// own terminal. The project's named commands come first (固定 under a name by someone in the
// project); under them, the shell commands the task's AI teammate ran, newest first, each with
// what it printed and whether it failed.
//
// 「再运行」 hands the command up; where it runs is the page's business. 「固定」 asks for a name
// in place and hands both up. The list is read when it opens (`open`), so a closed button costs
// nothing.
import type { NamedCommand, RanCommand } from '@/api'

import { nextTick, ref } from 'vue'

import { t } from '../../i18n'
import { dayTimeText } from '../../lib/dateText'
import BaseButton from '../base/BaseButton.vue'

defineProps<{
  named: NamedCommand[]
  ran: RanCommand[]
  /** There is an older page of commands. */
  more: boolean
  loading: boolean
  failed: boolean
  nameFailed: boolean
  /** The project can keep a named command (a task outside a project cannot). */
  canName: boolean
  agentName: string
}>()
const emit = defineEmits<{
  open: []
  run: [command: string]
  name: [label: string, command: string, done: (kept: boolean) => void]
  remove: [id: string]
  more: []
}>()

const open = ref(false)
/** The row whose output is shown. */
const expanded = ref<string | null>(null)
/** The row being named, and the name typed so far. */
const naming = ref<string | null>(null)
const label = ref('')
const field = ref<HTMLInputElement[] | HTMLInputElement | null>(null)

function toggle(now: boolean) {
  open.value = now
  if (now) emit('open')
  else naming.value = null
}

function run(command: string) {
  open.value = false
  naming.value = null
  emit('run', command)
}

async function startNaming(row: RanCommand) {
  naming.value = row.call_id
  label.value = ''
  await nextTick()
  const input = Array.isArray(field.value) ? field.value[0] : field.value
  input?.focus()
}

function saveName(row: RanCommand) {
  const name = label.value.trim()
  if (!name) return
  emit('name', name, row.command, (kept) => {
    if (kept && naming.value === row.call_id) naming.value = null
  })
}

function when(at: string): string {
  return dayTimeText(Date.parse(at))
}
</script>

<template>
  <v-menu :model-value="open" location="top end" :close-on-content-click="false" @update:model-value="toggle">
    <template #activator="{ props: menuProps }">
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-history"
        v-bind="menuProps"
        :aria-label="t('work.room.runs.title')"
        :title="t('work.room.runs.title')"
        data-testid="run-records-open"
      />
    </template>
    <div class="runs" role="dialog" :aria-label="t('work.room.runs.title')">
      <section v-if="named.length" class="runs__group">
        <h3 class="runs__head">{{ t('work.room.runs.named') }}</h3>
        <ul class="runs__list">
          <li v-for="row in named" :key="row.id" class="runs__row">
            <div class="runs__main">
              <span class="runs__name">{{ row.name }}</span>
              <code class="runs__command" :title="row.command">{{ row.command }}</code>
            </div>
            <div class="runs__actions">
              <BaseButton kind="secondary" size="sm" @click="run(row.command)">{{
                t('work.room.runs.run')
              }}</BaseButton>
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-close"
                :aria-label="t('work.room.runs.remove', { name: row.name })"
                :title="t('work.room.runs.remove', { name: row.name })"
                @click="emit('remove', row.id)"
              />
            </div>
          </li>
        </ul>
      </section>
      <section class="runs__group">
        <h3 class="runs__head">{{ t('work.room.runs.ran', { name: agentName }) }}</h3>
        <ul v-if="ran.length" class="runs__list">
          <li v-for="row in ran" :key="row.call_id" class="runs__row runs__row--ran">
            <button
              type="button"
              class="runs__main runs__open"
              :aria-expanded="expanded === row.call_id"
              @click="expanded = expanded === row.call_id ? null : row.call_id"
            >
              <code class="runs__command" :title="row.command">{{ row.command }}</code>
              <span class="runs__meta">
                <span v-if="row.description" class="runs__description">{{ row.description }}</span>
                <span>{{ when(row.at) }}</span>
                <span v-if="row.failed" class="runs__failed">{{ t('work.room.runs.failed') }}</span>
              </span>
            </button>
            <div class="runs__actions">
              <BaseButton kind="secondary" size="sm" @click="run(row.command)">{{
                t('work.room.runs.run')
              }}</BaseButton>
              <BaseButton v-if="canName" kind="ghost" size="sm" @click="startNaming(row)">
                {{ t('work.room.runs.pin') }}
              </BaseButton>
            </div>
            <div v-if="naming === row.call_id" class="runs__naming">
              <input
                ref="field"
                v-model="label"
                class="runs__field"
                maxlength="40"
                autocomplete="off"
                :aria-label="t('work.room.runs.nameLabel')"
                :placeholder="t('work.room.runs.nameLabel')"
                @keydown.enter.prevent="saveName(row)"
                @keydown.esc.stop="naming = null"
              />
              <BaseButton kind="secondary" size="sm" :disabled="!label.trim()" @click="saveName(row)">
                {{ t('work.room.runs.save') }}
              </BaseButton>
              <span v-if="nameFailed" class="runs__failed">{{ t('work.room.runs.nameFailed') }}</span>
            </div>
            <pre v-if="expanded === row.call_id" class="runs__output">{{
              row.output ?? (row.background ? t('work.room.runs.background') : t('work.room.runs.running'))
            }}</pre>
          </li>
        </ul>
        <p v-else-if="loading" class="runs__note">{{ t('work.room.runs.loading') }}</p>
        <p v-else-if="!failed" class="runs__note">{{ t('work.room.runs.empty') }}</p>
        <p v-if="failed" class="runs__note runs__failed">{{ t('work.room.runs.readFailed') }}</p>
        <BaseButton v-if="more && !failed" kind="ghost" size="sm" :loading="loading" @click="emit('more')">
          {{ t('work.room.runs.older') }}
        </BaseButton>
      </section>
    </div>
  </v-menu>
</template>

<style scoped>
.runs {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: min(480px, calc(100vw - 32px));
  max-height: 480px;
  overflow-y: auto;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.runs__group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.runs__head {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  color: var(--muted);
}
.runs__list {
  display: flex;
  flex-direction: column;
  align-self: stretch;
  margin: 0;
  padding: 0;
  list-style: none;
}
.runs__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px 8px;
  padding: 6px 0;
  border-top: 1px solid var(--line);
}
.runs__row:first-child {
  border-top: 0;
}
.runs__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.runs__open {
  padding: 2px 4px;
  margin: -2px -4px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.runs__open:hover {
  background: var(--fill);
}
.runs__name {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}
.runs__command {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runs__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.runs__description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runs__failed {
  color: var(--danger-ink);
}
.runs__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.runs__naming {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: var(--lh-12);
}
.runs__field {
  flex: 1 1 auto;
  min-width: 0;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
}
.runs__field:focus {
  border-color: var(--muted);
  outline: none;
}
.runs__output {
  grid-column: 1 / -1;
  max-height: 200px;
  margin: 0;
  overflow: auto;
  padding: 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre-wrap;
  word-break: break-all;
}
.runs__note {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
