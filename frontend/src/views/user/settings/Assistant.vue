<script setup lang="ts">
/**
 * 「芝士」（`/users/settings/assistant`）。取数、保存在这里，画法在同目录的
 * `AssistantView.vue`。勾选「一定先问我」改一下存一下；两段话点「保存」才存。
 */
import type { AssistantActivityItem, AssistantRules } from '@/api'

import { onMounted, ref } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import AssistantView from './AssistantView.vue'

import { getAssistantActivity, getAssistantRules, saveAssistantRules } from '@/api'
import { t } from '@/i18n'

const rules = ref<AssistantRules | null>(null)
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
    const [got, done] = await Promise.all([getAssistantRules(), getAssistantActivity()])
    rules.value = got
    recent.value = done.items
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function save(next: Pick<AssistantRules, 'ask' | 'allowText' | 'askText'>) {
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

function toggle(code: string) {
  const current = rules.value
  if (!current) return
  const ask = current.ask.includes(code) ? current.ask.filter((c) => c !== code) : [...current.ask, code]
  void save({ ask, allowText: current.allowText, askText: current.askText })
}

function saveWords(words: { allowText: string; askText: string }) {
  const current = rules.value
  if (current) void save({ ask: current.ask, ...words })
}
</script>

<template>
  <AssistantView
    :rules="rules"
    :recent="recent"
    :loading="loading"
    :failed="failed"
    :saving="saving"
    :saved="saved"
    :error="error"
    @retry="load"
    @toggle="toggle"
    @save-words="saveWords"
  />
</template>
