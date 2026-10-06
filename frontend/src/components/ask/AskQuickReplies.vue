<script setup lang="ts">
/**
 * The quick replies under a question 芝士 asked (`cheese_ask`).
 *
 * A question is an ordinary message; its options are shortcuts for a reply, not
 * a form. Clicking one emits `reply` with the option's text and the panel sends
 * it as a message replying to the question — exactly what typing it would do.
 * Typing anything else in the input box answers too, so nothing here takes the
 * composer over. Once anyone has answered, the buttons give way to who said what.
 */
import type { Block } from '../../cx_types'

import { computed } from 'vue'

import { t } from '../../i18n'
import { askAnswers, askOptions } from '../../lib/blockDisplay'

const props = defineProps<{
  block: Block
  names: Record<string, string>
}>()
const emit = defineEmits<{ (e: 'reply', text: string): void }>()

const options = computed(() => askOptions(props.block) ?? [])
const answers = computed(() => askAnswers(props.block))

// Models are told to put their pick first and may mark it this way (Codex's
// convention); the mark is shown as a tag and is not part of what gets sent.
const RECOMMENDED = ' (Recommended)'
const label = (text: string) => (text.endsWith(RECOMMENDED) ? text.slice(0, -RECOMMENDED.length) : text)
const recommended = (text: string) => text.endsWith(RECOMMENDED)
</script>

<template>
  <div class="ask-replies">
    <ul v-if="answers.length" class="ask-replies__answers t-meta">
      <li v-for="(answer, index) in answers" :key="index">
        {{ t('ask.replies.answered', { name: names[answer.by] ?? answer.by, text: answer.text }) }}
      </li>
    </ul>
    <div v-else class="ask-replies__options" role="group" :aria-label="t('ask.replies.label')">
      <button
        v-for="option in options"
        :key="option.text"
        type="button"
        class="ask-replies__option"
        @click="emit('reply', label(option.text))"
      >
        <span class="ask-replies__text">{{ label(option.text) }}</span>
        <span v-if="recommended(option.text)" class="ask-replies__tag">{{ t('ask.replies.recommended') }}</span>
        <span v-if="option.explain" class="ask-replies__explain">{{ option.explain }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.ask-replies {
  margin-top: 8px;
}
.ask-replies__options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ask-replies__option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  max-width: 100%;
  padding: 6px 12px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ask-replies__option:hover {
  background: var(--fill);
}
.ask-replies__text {
  color: var(--ink);
}
.ask-replies__tag {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.ask-replies__explain {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.ask-replies__answers {
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--muted);
}
</style>
