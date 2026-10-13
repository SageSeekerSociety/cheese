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
 * Whoever asked fixed the options, so the answer someone most needs to give is
 * often the one that is not among them. An "其他" chip after the options opens
 * a field right here for that: it sends the person's own words and never the
 * chip's label, so the answer is recorded as something they said rather than as
 * a choice.
 *
 * The agent chooses whom it asks, as a message @-mentions people: one person,
 * several, or everyone in the channel. The card says so above the options
 * (「问 @A @B」), with the same chips a message draws.
 *
 * A person it asks can decline to answer: the ✕ beside the options skips it
 * for him. Nothing is said in the room; the question stops waiting on him, the
 * options go away for him only, and the others it asks still see them. When
 * everyone it names has skipped, nobody sees options any more and the agent
 * that asked is told; a question asked of everyone never ends that way. Only
 * whoever may answer sees the ✕ — someone it asks, or anyone when it asks
 * nobody in particular (the server holds the same rule). A skip is undone at
 * once, without a confirmation: it is his own, and undoing it only puts the
 * options back in front of him.
 *
 * The one who answered can take it back: a question takes one answer, so a
 * misclick otherwise has no way out. That is the answerer's own line — nobody
 * else's — and it comes off the question, not out of the room: the message they
 * sent stays, and what they typed there stays with it. Confirmed first, because
 * the question goes back to waiting and everyone sees the options again.
 */
import type { Block } from '../../cx_types'

import { computed, nextTick, ref } from 'vue'

import { t } from '../../i18n'
import { askAnswers, askOptions } from '../../lib/blockDisplay'
import BaseButton from '../base/BaseButton.vue'
import ConfirmDialog from '../base/ConfirmDialog.vue'
import UserRef from '../common/UserRef.vue'

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
  /** 撤回自己的答复，或撤销自己的跳过：服务端是同一个动作。 */
  (e: 'withdraw'): void
  (e: 'skip'): void
}>()

const options = computed(() => askOptions(props.block) ?? [])
const meta = computed(() => (props.block.meta ?? {}) as { asked?: string[]; asked_all?: boolean })
/** 问了谁：点名的那几个人；问所有人时只画一颗「@所有人」。空是没点名。 */
const asked = computed(() => meta.value.asked ?? [])
const askedAll = computed(() => !!meta.value.asked_all)
const responses = computed(() => askAnswers(props.block))
/** 真正的回答（不是跳过）：有一条，题就对所有人了结了。 */
const answers = computed(() => responses.value.filter((a) => !a.skipped))
const skips = computed(() => responses.value.filter((a) => a.skipped))
// 点名的人都跳过了：题对谁都了结了。问所有人的题不会这样了结。
const skippedByAll = computed(
  () => !askedAll.value && asked.value.length > 0 && asked.value.every((h) => skips.value.some((s) => s.by === h))
)
const mySkip = computed(() => props.viewer != null && skips.value.some((s) => s.by === props.viewer))
/** 跳过的那几行：了结时列出每个人，没了结时只列看的人自己那一条。 */
const skipLines = computed(() => (skippedByAll.value ? skips.value : skips.value.filter((s) => s.by === props.viewer)))
// 带说明的选项是几段话，不是几个词：按各自内容收缩时三张卡宽窄不一，读起来像排版
// 出了错。这时排成一列、同宽；只有短短几个词的选项才横着排成一行按钮。
const stacked = computed(() => options.value.some((option) => option.explain))

// 只有自己答的那一条能撤回。题已经不再等回答（任务关了）也照给：撤回是回答的人
// 一直有的权利，后端不看这两件事。
const mine = computed(() => props.viewer != null && answers.value.some((a) => a.by === props.viewer))
// 跳过只给这道题在等的人：题点了名就是名单上的人，没点名就谁都行。
const canSkip = computed(() => !!props.viewer && (asked.value.length === 0 || asked.value.includes(props.viewer)))
const confirming = ref(false)

function confirmWithdraw() {
  confirming.value = false
  emit('withdraw')
}

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
    <p v-if="asked.length" class="ask-replies__asked t-meta">
      <i18n-t keypath="ask.replies.asks" tag="span" scope="global">
        <template #people>
          <UserRef v-if="askedAll" handle="all" :name="names.all" />
          <template v-else>
            <template v-for="(handle, index) in asked" :key="handle">
              <template v-if="index">&#32;</template><UserRef :handle="handle" :name="names[handle]" />
            </template>
          </template>
        </template>
      </i18n-t>
    </p>
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
    <template v-else>
      <ul v-if="skipLines.length" class="ask-replies__answers t-meta">
        <li v-for="skip in skipLines" :key="skip.by">
          <span class="ask-replies__answer">{{ t('ask.replies.skipped', { name: names[skip.by] ?? skip.by }) }}</span>
          <button
            v-if="skip.by === viewer"
            type="button"
            class="ask-replies__withdraw"
            :aria-label="t('ask.replies.undoSkipAria')"
            @click="emit('withdraw')"
          >
            {{ t('ask.replies.undoSkip') }}
          </button>
        </li>
      </ul>
      <p v-if="closed && !skippedByAll" class="ask-replies__closed t-meta">{{ t('ask.replies.closed') }}</p>
      <div v-else-if="!skippedByAll && !mySkip" class="ask-replies__open">
        <div
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
        <BaseButton
          v-if="canSkip"
          class="ask-replies__skip"
          kind="ghost"
          size="sm"
          icon="mdi-close"
          :aria-label="t('ask.replies.skip')"
          :title="t('ask.replies.skip')"
          @click="emit('skip')"
        />
      </div>
    </template>
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
/* 跳过的 ✕ 贴在选项组右边：横排时在一行按钮之后，竖排时在第一张选项卡的右上角。 */
.ask-replies__open {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
/* 按内容取宽，✕ 才紧跟在按钮后面，而不是被推到整行最右。 */
.ask-replies__open > .ask-replies__options {
  flex: 0 1 auto;
  min-width: 0;
}
.ask-replies__skip {
  flex: none;
}
.ask-replies__asked {
  margin: 0 0 4px;
  color: var(--muted);
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
