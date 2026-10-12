<script setup lang="ts">
/**
 * The quick replies under a question 芝士 asked (`cheese_ask`).
 *
 * A question is an ordinary message; its options are shortcuts for a reply, not
 * a form. Clicking one emits `reply` with the option's text and the panel sends
 * it as a message replying to the question — exactly what typing it would do.
 * Typing anything else in the input box answers too, so nothing here takes the
 * composer over. Once anyone has answered, the buttons give way to who said what.
 * Where nobody can answer any more (its task closed, its channel archived) the
 * question stays as it was asked and says it no longer waits for an answer.
 *
 * The one who answered can take it back: a question takes one answer, so a
 * misclick otherwise has no way out. That is the answerer's own line — nobody
 * else's — and it comes off the question, not out of the room: the message they
 * sent stays, and what they typed there stays with it. Confirmed first, because
 * the question goes back to waiting and everyone sees the options again.
 */
import type { Block } from '../../cx_types'

import { computed, ref } from 'vue'

import { t } from '../../i18n'
import { askAnswers, askOptions } from '../../lib/blockDisplay'
import ConfirmDialog from '../base/ConfirmDialog.vue'

const props = defineProps<{
  block: Block
  names: Record<string, string>
  /** 这里已经不能回答了：任务关了、频道归档了。 */
  closed?: boolean
  /** 看着这间房的人（自己的 handle）：哪一条答案旁边有「撤回」由它定。 */
  viewer?: string
}>()
const emit = defineEmits<{
  (e: 'reply', text: string): void
  (e: 'withdraw'): void
}>()

const options = computed(() => askOptions(props.block) ?? [])
const answers = computed(() => askAnswers(props.block))
// 带说明的选项是几段话，不是几个词：按各自内容收缩时三张卡宽窄不一，读起来像排版
// 出了错。这时排成一列、同宽；只有短短几个词的选项才横着排成一行按钮。
const stacked = computed(() => options.value.some((option) => option.explain))

// 只有自己答的那一条能撤回。题已经不再等回答（任务关了）也照给：撤回是回答的人
// 一直有的权利，后端不看这两件事。
const mine = computed(() => props.viewer != null && answers.value.some((a) => a.by === props.viewer))
const confirming = ref(false)

function confirmWithdraw() {
  confirming.value = false
  emit('withdraw')
}

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
        <span class="ask-replies__answer">{{
          t('ask.replies.answered', { name: names[answer.by] ?? answer.by, text: answer.text })
        }}</span>
        <button
          v-if="answer.by === viewer"
          type="button"
          class="ask-replies__withdraw"
          :aria-label="t('ask.replies.withdrawAria', { text: answer.text })"
          @click="confirming = true"
        >
          {{ t('ask.replies.withdraw') }}
        </button>
      </li>
    </ul>
    <p v-else-if="closed" class="ask-replies__closed t-meta">{{ t('ask.replies.closed') }}</p>
    <div
      v-else
      class="ask-replies__options"
      :class="{ 'ask-replies__options--stacked': stacked }"
      role="group"
      :aria-label="t('ask.replies.label')"
    >
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
    <!-- 先弹一下确认：撤错了，这道题就当着全房间的面重新长出选项，比多点一次麻烦。 -->
    <ConfirmDialog
      v-if="mine"
      v-model="confirming"
      :title="t('ask.replies.withdrawTitle')"
      :confirm-label="t('ask.replies.withdraw')"
      danger
      @confirm="confirmWithdraw"
    >
      {{ t('ask.replies.withdrawBody') }}
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.ask-replies {
  margin-top: 8px;
}
.ask-replies__closed {
  margin: 0;
  color: var(--muted);
}
.ask-replies__options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ask-replies__options--stacked {
  flex-direction: column;
  max-width: 640px;
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
/* 选项和说明是模型写的，常带一个 URL 或一长串不断开的词：窄屏上它会撑出卡片。 */
.ask-replies__text,
.ask-replies__explain {
  overflow-wrap: anywhere;
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
.ask-replies__answer {
  overflow-wrap: anywhere;
}
.ask-replies__withdraw {
  margin-left: 8px;
  padding: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: none;
  border: 0;
  text-decoration: underline;
}
.ask-replies__withdraw:hover {
  color: var(--text);
}
</style>
