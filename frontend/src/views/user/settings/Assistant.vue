<script setup lang="ts">
/**
 * 「芝士」（`/users/settings/assistant`）。取数、保存在这里，画法在同目录的
 * `AssistantView.vue`。勾选、添加、删除一条都是改一下存一下。
 */
import type { AssistantActivityItem, AssistantRoutine, AssistantRules } from '@/api'

import { onMounted, ref } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import AssistantView from './AssistantView.vue'

import {
  getAssistantActivity,
  getAssistantRoutines,
  getAssistantRules,
  saveAssistantRules,
  setAssistantRoutine,
} from '@/api'
import { t } from '@/i18n'
import { openAgentConversation } from '@/stores/agent'

const rules = ref<AssistantRules | null>(null)
const routines = ref<AssistantRoutine[]>([])
const routineLimit = ref(0)
const recent = ref<AssistantActivityItem[]>([])
const loading = ref(true)
const failed = ref(false)

const { saving, saved, error, run } = useSaveState({
  feedback: 'inline',
  messages: { failed: t('account.assistant.saveFailed') },
  describeError: () => t('account.assistant.saveFailed'),
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    const [got, done, mine] = await Promise.all([getAssistantRules(), getAssistantActivity(), getAssistantRoutines()])
    rules.value = got
    recent.value = done.items
    routines.value = mine.routines
    routineLimit.value = mine.limit
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

type Lists = Pick<AssistantRules, 'ask' | 'askItems' | 'allowItems'>

async function save(next: Lists) {
  const previous = rules.value
  if (!previous) return
  rules.value = { ...previous, ...next }
  await run(async () => {
    try {
      rules.value = await saveAssistantRules(next)
    } catch (e) {
      rules.value = previous
      throw e
    }
  })
}

function lists(): Lists | null {
  const current = rules.value
  return current ? { ask: current.ask, askItems: current.askItems, allowItems: current.allowItems } : null
}

function toggle(code: string) {
  const now = lists()
  if (!now) return
  const ask = now.ask.includes(code) ? now.ask.filter((c) => c !== code) : [...now.ask, code]
  void save({ ...now, ask })
}

function addItem(list: 'ask' | 'allow', text: string) {
  const now = lists()
  if (!now) return
  if (list === 'ask') void save({ ...now, askItems: [...now.askItems, text] })
  else void save({ ...now, allowItems: [...now.allowItems, text] })
}

function removeItem(list: 'ask' | 'allow', index: number) {
  const now = lists()
  if (!now) return
  if (list === 'ask') void save({ ...now, askItems: now.askItems.filter((_, i) => i !== index) })
  else void save({ ...now, allowItems: now.allowItems.filter((_, i) => i !== index) })
}

async function routine(id: string, action: 'pause' | 'resume' | 'delete') {
  await run(async () => {
    const now = await setAssistantRoutine(id, action)
    routines.value = now.routines
  })
}
</script>

<template>
  <AssistantView
    :rules="rules"
    :routines="routines"
    :routine-limit="routineLimit"
    :recent="recent"
    :loading="loading"
    :failed="failed"
    :saving="saving"
    :saved="saved"
    :error="error"
    @retry="load"
    @toggle="toggle"
    @add-item="addItem"
    @remove-item="removeItem"
    @routine="routine"
    @open-conversation="openAgentConversation"
  />
</template>
