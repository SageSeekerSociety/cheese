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
 * often the one that is not among them. The last row under the options is a
 * field for that, there from the start like the options beside it (as in Claude's
 * own ask-a-question card): typing in it and pressing Enter, or its send button,
 * sends the person's own words as the answer, recorded as something they said
 * rather than as a choice.
 *
 * The agent chooses whom it asks, as a message @-mentions people: one person,
 * several, or everyone in the channel. The card says so above the options
 * (「问 @A @B」), with the same chips a message draws.
 *
 * Only whoever may answer it can use the options, the field and the ✕: someone it
 * asks, anyone in the channel when it asks everyone, and anyone when it asks
 * nobody in particular (the server holds the same rule, `block.asked.may_answer`,
 * for a click, a typed answer and a skip alike). Anyone else sees the options
 * and the field greyed out, without the ✕; the 「问 @…」 line above them says whom it waits on.
 *
 * A person it asks can decline to answer: the ✕ beside the options skips it
 * for him. Nothing is said in the room; the question stops waiting on him, the
 * options go away for him only, and the others it asks still see them. When
 * everyone it names has skipped, nobody sees options any more and the agent
 * that asked is told; a question asked of everyone never ends that way. A skip
 * is undone at once, without a confirmation: it is his own, and undoing it only
 * puts the options back in front of him.
 *
 * The one who answered can take it back: a question takes one answer, so a
 * misclick otherwise has no way out. That is the answerer's own line — nobody
 * else's — and it comes off the question, not out of the room: the message they
 * sent stays, and what they typed there stays with it. Confirmed first, because
 * the question goes back to waiting and everyone sees the options again.
 */
import type { Block } from '../../cx_types'

import { computed, ref, watch } from 'vue'

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
// 点选项、在末行写自己的答案、跳过，都只给能回答这道题的人：点了名就是名单上的人，问所有人
// 就是频道里谁都行（名单是问出口那一刻的人，后来进频道的也算），没点名也是谁都行。
// 和服务端 `block.asked.may_answer` 同一条。
const canAnswer = computed(
  () => !!props.viewer && (asked.value.length === 0 || askedAll.value || asked.value.includes(props.viewer))
)
const confirming = ref(false)

function confirmWithdraw() {
  confirming.value = false
  emit('withdraw')
}

// 末行那个输入框就是「其他」：一直摆在选项下面，打字就能答，不用先点开（Claude
// 自己的提问卡也是这样）。发出去的是他打的那句话，和下面那个输入框打出来的是同一条路。
const otherText = ref('')
const otherField = ref<HTMLTextAreaElement | null>(null)

// 一行起步，打出第二行就跟着长高，不在一行高的框里滚动。
function fitOther() {
  const el = otherField.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}
// 浏览器给一行高的 textarea 留的高度比一行字多：框一画出来就量一次，空着时才和选项
// 卡一样高。撤回之后选项重新长出来，也是一个新框。
watch(otherField, fitOther, { flush: 'post' })

function sendOther() {
  const text = otherText.value.trim()
  if (!text || !canAnswer.value) return
  otherText.value = ''
  emit('reply', text)
  requestAnimationFrame(fitOther)
}

function onOtherKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey) return
  // 输入法选字时的回车是给输入法的（keydown 也带着它）：别顺手把这条答案发出去。
  // Shift+回车照常换行，所以上面先放它过去。
  e.preventDefault()
  if (e.isComposing || e.keyCode === 229) return
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
        <div class="ask-replies__options" role="group" :aria-label="t('ask.replies.label')">
          <div class="ask-replies__choices" :class="{ 'ask-replies__choices--stacked': stacked }">
            <button
              v-for="option in options"
              :key="option.text"
              type="button"
              class="ask-replies__option"
              :disabled="!canAnswer"
              @click="emit('reply', label(option.text))"
            >
              <span class="ask-replies__text">{{ label(option.text) }}</span>
              <span v-if="recommended(option.text)" class="ask-replies__tag">{{ t('ask.replies.recommended') }}</span>
              <span v-if="option.explain" class="ask-replies__explain">{{ option.explain }}</span>
            </button>
          </div>
          <div class="ask-replies__other" :class="{ 'ask-replies__other--off': !canAnswer }">
            <textarea
              ref="otherField"
              v-model="otherText"
              rows="1"
              autocomplete="off"
              class="ask-replies__other-input"
              :disabled="!canAnswer"
              :placeholder="t('ask.replies.otherPlaceholder')"
              :aria-label="t('ask.replies.otherPlaceholder')"
              @input="fitOther"
              @keydown="onOtherKey"
            />
            <BaseButton
              class="ask-replies__other-send"
              kind="ghost"
              size="sm"
              icon="mdi-arrow-up"
              :disabled="!canAnswer || !otherText.trim()"
              :aria-label="t('ask.replies.otherSend')"
              :title="t('ask.replies.otherSend')"
              @click="sendOther"
            />
          </div>
        </div>
        <BaseButton
          v-if="canAnswer"
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
/* 选项在上，末行的输入框在下：一列，宽度取两者里宽的那个，输入框和竖排的选项卡
   一起拉到同宽。 */
.ask-replies__options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 640px;
}
.ask-replies__choices {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ask-replies__choices--stacked {
  flex-direction: column;
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
.ask-replies__option:hover:not(:disabled) {
  background: var(--fill);
}
/* 问的不是看的人：选项照样读得到，只是降一档、点不动；上面那行「问 @…」说了在等谁。
   末行的输入框也照这样灰。 */
.ask-replies__option:disabled {
  cursor: default;
}
.ask-replies__option:disabled .ask-replies__text {
  color: var(--muted);
}
.ask-replies__option:disabled .ask-replies__explain,
.ask-replies__option:disabled .ask-replies__tag {
  color: var(--faint);
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
/* 末行的输入框和选项是同一种卡：同样的边、底和圆角，总在选项下面自成一行。聚焦时
   只把边提一档，和别处的输入框一样。 */
.ask-replies__other {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  min-width: 0;
  padding: 1px 2px 1px 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.ask-replies__other:focus-within {
  border-color: var(--faint);
}
/* 自己要一个够写一句话的宽度：选项只有两三个短词时，整组也按它撑开，而窄屏上照样
   能缩。 */
.ask-replies__other-input {
  flex: 1 1 auto;
  width: 280px;
  min-width: 0;
  max-height: 160px;
  padding: 5px 0;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  background: transparent;
  border: 0;
  outline: none;
  resize: none;
}
.ask-replies__other-input::placeholder {
  color: var(--muted);
}
/* 问的不是看的人：和灰掉的选项同一种灰法。 */
.ask-replies__other--off .ask-replies__other-input::placeholder {
  color: var(--faint);
}
/* 图标按钮默认 40px，比一行字的框高：压到 28px 落进框里，空着的输入框才和单行的选项
   卡一样高。触屏上能点的范围 BaseButton 自己会撑到 44px。 */
.ask-replies__other-send {
  flex: none;
  width: 28px;
  height: 28px;
  margin-bottom: 2px;
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
