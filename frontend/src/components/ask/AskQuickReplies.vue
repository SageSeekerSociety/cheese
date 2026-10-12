<script setup lang="ts">
/**
 * The quick replies under a question 芝士 asked (`cheese_ask`).
 *
 * A question is an ordinary message; its options are shortcuts for a reply, not
 * a form. Clicking one emits `reply` with the option's text and the panel sends
 * it as a message replying to the question — exactly what typing it would do.
 * Typing anything else in the input box answers too, so nothing here takes the
 * composer over.
 *
 * Whoever asked fixed the options, so the answer someone most needs to give is
 * often the one that is not among them. An "其他" chip after the options opens
 * a field right here for that: it sends the person's own words and never the
 * chip's label, so the answer is recorded as something they said rather than as
 * a choice.
 *
 * Once anyone has answered, the buttons give way to who said what. Where nobody
 * can answer any more (its task closed, its channel archived) the question stays
 * as it was asked and says it no longer waits for an answer.
 */
import type { Block } from '../../cx_types'

import { computed, nextTick, ref } from 'vue'

import { t } from '../../i18n'
import { askAnswers, askOptions } from '../../lib/blockDisplay'

const props = defineProps<{
  block: Block
  names: Record<string, string>
  /** 这里已经不能回答了：任务关了、频道归档了。 */
  closed?: boolean
}>()
const emit = defineEmits<{ (e: 'reply', text: string): void }>()

const options = computed(() => askOptions(props.block) ?? [])
const answers = computed(() => askAnswers(props.block))
// 带说明的选项是几段话，不是几个词：按各自内容收缩时三张卡宽窄不一，读起来像排版
// 出了错。这时排成一列、同宽；只有短短几个词的选项才横着排成一行按钮。
const stacked = computed(() => options.value.some((option) => option.explain))

// 「其他」是个入口，不是第四个选项：点开只是在原处长出一个输入框，发出去的是他
// 自己打的那句话——和下面那个输入框打出来的是同一条路，绝不让「其他」两个字冒充
// 他的答案。
const otherOpen = ref(false)
const otherText = ref('')
const otherField = ref<HTMLInputElement | null>(null)

async function toggleOther() {
  otherOpen.value = !otherOpen.value
  if (otherOpen.value) {
    await nextTick()
    otherField.value?.focus()
  } else {
    otherText.value = ''
  }
}

function sendOther() {
  const text = otherText.value.trim()
  if (!text) return
  otherText.value = ''
  otherOpen.value = false
  emit('reply', text)
}

function onOtherKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    void toggleOther()
    return
  }
  if (e.key !== 'Enter') return
  // 输入法选字时的回车是给输入法的（keydown 也带着它）：别顺手把这条答案发出去。
  if (e.isComposing || e.keyCode === 229) {
    e.preventDefault()
    return
  }
  // 自己收这个回车，别让它再触发一次表单提交。
  e.preventDefault()
  sendOther()
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
        {{ t('ask.replies.answered', { name: names[answer.by] ?? answer.by, text: answer.text }) }}
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
      <button
        type="button"
        class="ask-replies__option ask-replies__other-toggle"
        :aria-expanded="otherOpen"
        @click="toggleOther"
      >
        <span class="ask-replies__text">{{ t('ask.replies.other') }}</span>
      </button>
      <form v-if="otherOpen" class="ask-replies__other" @submit.prevent="sendOther">
        <input
          ref="otherField"
          v-model="otherText"
          type="text"
          autocomplete="off"
          class="ask-replies__other-input"
          :placeholder="t('ask.replies.otherPlaceholder')"
          :aria-label="t('ask.replies.otherPlaceholder')"
          @keydown="onOtherKey"
        />
        <button type="submit" class="ask-replies__other-send" :disabled="!otherText.trim()">
          {{ t('ask.replies.otherSend') }}
        </button>
      </form>
    </div>
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
/* 「其他」展开时自己按下去，和选项的选中态同一档重。 */
.ask-replies__other-toggle[aria-expanded='true'] {
  background: var(--fill);
  border-color: var(--faint);
}
/* 输入框整行跟在选项后面：横排是一行选项下的第二行，竖排就是最后一行。 */
.ask-replies__other {
  display: flex;
  flex: 1 0 100%;
  gap: 8px;
  min-width: 0;
  max-width: 640px;
}
.ask-replies__other-input {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 12px;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
/* 聚焦只把边提一档，和别处的输入框一样：边本身就是焦点的指示。 */
.ask-replies__other-input:focus {
  outline: none;
  border-color: var(--faint);
}
.ask-replies__other-input::placeholder {
  color: var(--muted);
}
.ask-replies__other-send {
  flex: none;
  padding: 6px 12px;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ask-replies__other-send:hover:not(:disabled) {
  background: var(--line-2);
}
/* 空着没什么可发：发送键是灰的，而不是把空格当作一句答案发出去。 */
.ask-replies__other-send:disabled {
  color: var(--muted);
  cursor: default;
}
.ask-replies__answers {
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--muted);
}
</style>
