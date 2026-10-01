<script setup lang="ts">
import type { AskAnswerEntry } from '../../cx_types'

import { t } from '../../i18n'

defineProps<{ answers: AskAnswerEntry[]; names: Record<string, string> }>()
</script>

<template>
  <ol class="ask-history">
    <li v-for="answer in answers" :key="answer.v">
      <div class="ask-history__meta">
        {{ t('ask.history.version', { version: answer.v, name: names[answer.by] ?? answer.by }) }}
        <time v-if="answer.at" :datetime="answer.at">{{ new Date(answer.at).toLocaleString() }}</time>
        <span v-else>{{ t('ask.history.unknownTime') }}</span>
      </div>
      <p>
        {{ answer.kind === 'reject' ? t('ask.form.reject') : answer.kind === 'note' ? answer.note : answer.option }}
      </p>
      <p v-if="answer.kind !== 'note' && answer.note" class="ask-history__note">{{ answer.note }}</p>
    </li>
  </ol>
</template>

<style scoped>
.ask-history {
  display: grid;
  gap: 16px;
  padding: 0;
  list-style: none;
}
.ask-history__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}
.ask-history p {
  margin: 4px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ask-history__note {
  color: var(--muted);
}
</style>
