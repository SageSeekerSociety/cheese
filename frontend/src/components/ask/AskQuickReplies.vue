<script setup lang="ts">
/**
 * A question 芝士 asked (`cheese_ask`), drawn as one card: who asks whom, the
 * question as the card's title, its options as numbered rows, and a last row to
 * type an answer that is not among them.
 *
 * A question is an ordinary message; its options are shortcuts for a reply, not
 * a form. Clicking one (or pressing its number while the card has focus) emits
 * `reply` with the option's text and the panel sends it as a message replying to
 * the question — exactly what typing it would do. Typing anything else in the
 * input box answers too, so nothing here takes the composer over. Once anyone has
 * answered, the option rows give way to who said what. Where nobody can answer
 * any more (its task closed, its channel archived) the question stays as it was
 * asked and says it no longer waits for an answer.
 *
 * A question also stops waiting without anybody answering it: the teammate that
 * asked takes it back (`withdrawn`), or its deadline passes (`expired`). The
 * card then carries that line instead of the options. Taken back is final —
 * there is nothing to answer. Expired is only the clock: the options stay,
 * because a late answer still counts.
 *
 * Whoever asked fixed the options, so the answer someone most needs to give is
 * often the one that is not among them. The last row is a field for that, there
 * from the start like the options above it (as in Claude's own ask-a-question
 * card): typing in it and pressing Enter, or its send button, sends the person's
 * own words as the answer, recorded as something they said rather than as a
 * choice.
 *
 * The agent chooses whom it asks: one person, several, or everyone in the
 * channel. The card's header says so in words (「芝士 问你」「芝士 问 Alice」). It is
 * a statement about who is asked, not a mention, so the names are plain text,
 * never the @ chips a message draws.
 *
 * Only whoever may answer it can use the options, the field and the ✕: someone it
 * asks, anyone in the channel when it asks everyone, and anyone when it asks
 * nobody in particular (the server holds the same rule, `block.asked.may_answer`,
 * for a click, a typed answer and a skip alike). Anyone else sees the options
 * and the field greyed out, without the ✕; the header says whom it waits on.
 *
 * A person it asks can decline to answer: the ✕ at the card's top right skips it
 * for him. Nothing is said in the room; the question stops waiting on him, the
 * options go away for him only, and the others it asks still see them. When
 * everyone it names has skipped, nobody sees options any more and the agent
 * that asked is told; a question asked of everyone never ends that way. A skip
 * is undone at once, without a confirmation: it is his own, and undoing it only
 * puts the options back in front of him.
 *
 * Some questions carry the platform's buttons instead of plain options: a
 * teammate asking a person to decide a delivery that waits on them
 * (`agent.ask.ask_to_decide`) offers the card's accept and the task's close,
 * under the very names those buttons have (`acceptLabelKey`, 「关闭任务」). Such a button
 * is not a reply: clicking it emits `decide` and the panel calls the card's own
 * accept or the task's own close, the same request the card's and the task's
 * buttons make. On a task page the accept button reads the very state the
 * delivery bar's 采纳 reads (`injectAcceptCard`): the same label, greyed out
 * whenever the bar's is and with the same reason on hover, and a click is the
 * bar's own `onAcceptCard`. The question then closes when the delivery is decided, however
 * it was decided (`decided`), and says how; that line is the card's, so nobody
 * takes it back.
 *
 * The one who answered can take it back: a question takes one answer, so a
 * misclick otherwise has no way out. That is the answerer's own line — nobody
 * else's — and it comes off the question, not out of the room: the message they
 * sent stays, and what they typed there stays with it. Confirmed first, because
 * the question goes back to waiting and everyone sees the options again.
 *
 * Answering is a message, and the card only grows into its answered shape when
 * that message lands and the question comes back with it. Until then it still
 * looks unanswered, so it locks itself the moment an option or the field is
 * used: a second click in that window is the same message sent twice, and the
 * room really would gain a second message. The lock shows as the options, the
 * field and the ✕ being dead, the same way they look to someone the question
 * does not wait on. It lifts when that answer stops being on its way — it landed,
 * or the server refused it and the line sits in the room with 「重试」.
 */
import type { Block } from '../../cx_types'
import type { AskOption } from '../../lib/blockDisplay'
import type { AskAction } from '../../types/ask'

import { computed, ref, watch } from 'vue'

import { acceptLabelKey } from '../../composables/acceptCard/labels'
import { injectAcceptCard } from '../../composables/useAcceptCard'
import { t } from '../../i18n'
import { askAnswers, askClosedByAsker, askOptions } from '../../lib/blockDisplay'
import BaseButton from '../base/BaseButton.vue'
import ConfirmDialog from '../base/ConfirmDialog.vue'

const props = defineProps<{
  block: Block
  /**
   * 问的那一位的显示名（卡片页头「芝士 问你」里的「芝士」）。卡片正上方已经是这位的
   * 名字那一行（一串消息的第一条）时不给：页头只写「问你」，名字不说两遍。
   */
  asker?: string
  names: Record<string, string>
  /** 这里已经不能回答了：任务关了、频道归档了。 */
  closed?: boolean
  /**
   * 我在这道题上有一条答复还压在发件箱里、题上还没记下：选项先按住。
   * 谁在算它见 `ChatTimeline.answeringQuestion`。
   */
  answering?: boolean
  /** 看着这间房的人（自己的 handle）：哪一条答案旁边有「撤回」由它定。 */
  viewer?: string
}>()
const emit = defineEmits<{
  (e: 'reply', text: string): void
  /** 撤回自己的答复，或撤销自己的跳过：服务端是同一个动作。 */
  (e: 'withdraw'): void
  (e: 'skip'): void
  /** 点了平台给的按钮（接受交付、关闭任务）：做那个动作，不是回一句话。 */
  (e: 'decide', action: AskAction): void
}>()

const options = computed(() => askOptions(props.block) ?? [])
const meta = computed(() => (props.block.meta ?? {}) as { asked?: string[]; asked_all?: boolean })
/** 问了谁：点名的那几个人。空是没点名。 */
const asked = computed(() => meta.value.asked ?? [])
const askedAll = computed(() => !!meta.value.asked_all)
const responses = computed(() => askAnswers(props.block))
/** 提问方把题收起来了：撤回（不再等人答）或过期（还能答）。 */
const closedByAsker = computed(() => askClosedByAsker(props.block))
// 撤回了，或者按钮要决定的交付已经有了结果：都不再等人答，选项收起。
const withdrawn = computed(() => closedByAsker.value === 'withdrawn' || closedByAsker.value === 'decided')
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
// 只有自己答的那一条能撤回。题已经不再等回答（任务关了）也照给：撤回是回答的人
// 一直有的权利，后端不看这两件事。
const mine = computed(() => props.viewer != null && answers.value.some((a) => a.by === props.viewer && !a.decided))
// 点选项、在末行写自己的答案、跳过，都只给能回答这道题的人：点了名就是名单上的人，问所有人
// 就是频道里谁都行（名单是问出口那一刻的人，后来进频道的也算），没点名也是谁都行。
// 和服务端 `block.asked.may_answer` 同一条。
const canAnswer = computed(
  () => !!props.viewer && (asked.value.length === 0 || askedAll.value || asked.value.includes(props.viewer))
)
const confirming = ref(false)

// 页头那一句：谁问的、问谁。问到了看的人就说「你」；名字是平文，不是 @ 的 chip ——
// 这一句说的是在等谁，不是在叫谁。
const askedOthers = computed(() =>
  asked.value
    .filter((h) => h !== props.viewer)
    .map((h) => props.names[h] ?? h)
    .join(t('work.room.roster.listSeparator'))
)
const WHO = {
  named: {
    all: 'ask.replies.who.all',
    anyone: 'ask.replies.who.anyone',
    you: 'ask.replies.who.you',
    youAnd: 'ask.replies.who.youAnd',
    people: 'ask.replies.who.people',
  },
  bare: {
    all: 'ask.replies.whoBare.all',
    anyone: 'ask.replies.whoBare.anyone',
    you: 'ask.replies.whoBare.you',
    youAnd: 'ask.replies.whoBare.youAnd',
    people: 'ask.replies.whoBare.people',
  },
} as const
const whoKey = computed(() => {
  const keys = props.asker ? WHO.named : WHO.bare
  if (askedAll.value) return keys.all
  if (asked.value.length === 0) return keys.anyone
  if (props.viewer && asked.value.includes(props.viewer)) return asked.value.length > 1 ? keys.youAnd : keys.you
  return keys.people
})

// 选项还摆在看的人面前：没人答过、没结束、没被所有人跳过、他自己也没跳过，提问方
// 也没把它收回去。过期的那条不算：过期的题还能答，选项照给。
const choosing = computed(
  () => !answers.value.length && !props.closed && !skippedByAll.value && !mySkip.value && !withdrawn.value
)
// 选项换掉之后那一行说明。提问方收起来的先说它；过期的那条只是不再等人，选项还在
// 下面（见模板：这一行和选项可以同时出现）。
const closedLine = computed(() => {
  // 交付有了结果、而它相当于题上的某个按钮时，上面那一行答案已经说了是谁、点的哪个。
  if (closedByAsker.value === 'decided') return answers.value.length ? '' : t('ask.replies.decided')
  if (closedByAsker.value) return t(`ask.replies.${closedByAsker.value}`)
  if (props.closed && !skippedByAll.value) return t('ask.replies.closed')
  return ''
})

/**
 * 这道题的回答已经打出去了、题上还没看到它：这一段时间里选项不再收第二下。
 *
 * 拦在第一下的自己这一半（`sent`）点下去当场就生效，管的是同一帧里又冒出来的第二下
 * ——那时 DOM 上的 `disabled` 还没画上去。父层给的 `answering` 是发件箱里的实情，管
 * 的是卡片被重建（切话题、滚出屏幕再回来）之后本地那个记号留不住的时候，也说明这一
 * 趟什么时候结束：落库了、被服务端拒了，它都落回 false，本地这一半跟着松开。
 */
const sent = ref(false)
watch(
  () => props.answering,
  (answering) => {
    if (!answering) sent.value = false
  }
)
const locked = computed(() => sent.value || !!props.answering)

// 点一个选项：那几个字就是对这道题的回复。`disabled` 挡得住手，这里挡得住同一趟里
// 又冒出来的第二下（键盘的数字键也走这里）。
function pick(option: AskOption) {
  if (locked.value || !canAnswer.value || optionOff(option)) return
  // 平台的按钮不发消息：谁在做、做没做成由面板管（`useAskResponse.decide`），成了题自己
  // 会随交付的结果收起来。
  if (option.action) return emit('decide', option.action)
  sent.value = true
  emit('reply', label(option.text))
}

// 平台按钮上的字就是卡上「采纳」、任务「关闭任务」那两个按钮的字：同一个动作只有一个
// 名字。库里的 `text` 只记动作是哪一种。
function optionLabel(option: AskOption): string {
  const action = option.action
  const bound = boundAccept(option)
  if (bound) return bound.acceptLabel.value
  if (action?.kind === 'accept') return t(acceptLabelKey(action.completes_task))
  if (action?.kind === 'close') return t('work.task.close')
  return label(option.text)
}
// 任务页取的那一份采纳卡（交付栏读的就是它）。题上的「采纳」问的是同一张卡时，字、
// 灰不灰、为什么灰都读它，不另算一遍：同一个动作在同一屏上只有一种状态。卡已经不是
// 那一张（定了）时题也随之收起，用不到它。
const acceptCard = injectAcceptCard()
function boundAccept(option: AskOption) {
  const action = option.action
  if (action?.kind !== 'accept' || !acceptCard) return null
  return acceptCard.pendingCard.value?.id === action.card ? acceptCard : null
}
// 交付栏上的采纳按钮灰着（或者根本不给这个人点）时，题上这个也灰着。
function optionOff(option: AskOption): boolean {
  const bound = boundAccept(option)
  return !!bound && (!bound.decisionOpen.value || !!bound.acceptBlockedTitle.value || bound.acceptBusy.value)
}
// 为什么灰：交付栏按钮上那一句。
function optionTitle(option: AskOption): string | undefined {
  return boundAccept(option)?.acceptBlockedTitle.value ?? undefined
}

// 一行答案说的是哪个按钮时，也照看的人的语言写。
function answerText(text: string): string {
  const option = options.value.find((o) => o.action && o.text === text)
  return option ? optionLabel(option) : text
}

// 卡片有焦点时按 1–9 就是点第几个选项。末行输入框里的数字是在打字，不算；带修饰键的
// 是别的快捷键（⌘1–9 是切侧栏），也不算。
function onCardKey(e: KeyboardEvent) {
  if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.isComposing) return
  if (!/^[1-9]$/.test(e.key) || !choosing.value || !canAnswer.value || locked.value) return
  if ((e.target as HTMLElement).closest('textarea, input, [contenteditable="true"]')) return
  const option = options.value[Number(e.key) - 1]
  if (!option) return
  e.preventDefault()
  pick(option)
}

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
// 那几行一样高。撤回之后选项重新长出来，也是一个新框。
watch(otherField, fitOther, { flush: 'post' })

function sendOther() {
  const text = otherText.value.trim()
  if (!text || !canAnswer.value || locked.value) return
  sent.value = true
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
  <!-- tabindex=-1：点卡上任何一处，焦点就落在卡上，按数字键选选项（见 onCardKey）。 -->
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- digits are a shortcut caught as they bubble from the card; the option buttons are the keyboard path -->
  <section class="ask-replies" tabindex="-1" @keydown="onCardKey">
    <header class="ask-replies__head">
      <div class="ask-replies__heading">
        <p class="ask-replies__who">
          <i18n-t :keypath="whoKey" tag="span" scope="global">
            <template #asker>
              <span class="ask-replies__name">{{ asker }}</span>
            </template>
            <template #people>
              <span class="ask-replies__name">{{ askedOthers }}</span>
            </template>
          </i18n-t>
        </p>
        <div class="ask-replies__question">
          <slot>{{ block.content }}</slot>
        </div>
      </div>
      <BaseButton
        v-if="choosing && canAnswer && !locked"
        class="ask-replies__skip"
        kind="ghost"
        size="sm"
        icon="mdi-close"
        :aria-label="t('ask.replies.skip')"
        :title="t('ask.replies.skip')"
        @click="emit('skip')"
      />
    </header>
    <ul v-if="answers.length" class="ask-replies__answers">
      <li v-for="(answer, index) in answers" :key="index" class="ask-replies__row">
        <span class="ask-replies__answer">{{
          t('ask.replies.answered', { name: names[answer.by] ?? answer.by, text: answerText(answer.text) })
        }}</span>
        <button
          v-if="answer.by === viewer && !answer.decided"
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
      <ul v-if="skipLines.length" class="ask-replies__answers">
        <li v-for="skip in skipLines" :key="skip.by" class="ask-replies__row">
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
      <p v-if="closedLine" class="ask-replies__row ask-replies__closed">{{ closedLine }}</p>
      <div v-if="choosing" class="ask-replies__options" role="group" :aria-label="t('ask.replies.label')">
        <!-- 灰着的按钮不出 title，提示挂在外面这一层上（和交付栏的采纳按钮一样）。 -->
        <span
          v-for="(option, index) in options"
          :key="option.text"
          class="ask-replies__slot"
          :title="optionTitle(option)"
        >
          <button
            type="button"
            class="ask-replies__option"
            :disabled="!canAnswer || locked || optionOff(option)"
            @click="pick(option)"
          >
            <span class="ask-replies__num" aria-hidden="true">{{ index + 1 }}</span>
            <span class="ask-replies__body">
              <span class="ask-replies__label"
                ><span class="ask-replies__text">{{ optionLabel(option) }}</span
                ><span v-if="recommended(option.text)" class="ask-replies__tag">{{
                  t('ask.replies.recommended')
                }}</span></span
              >
              <span v-if="option.explain" class="ask-replies__explain">{{ option.explain }}</span>
            </span>
          </button>
        </span>
        <div class="ask-replies__other" :class="{ 'ask-replies__other--off': !canAnswer || locked }">
          <textarea
            ref="otherField"
            v-model="otherText"
            rows="1"
            autocomplete="off"
            class="ask-replies__other-input"
            :disabled="!canAnswer || locked"
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
            :disabled="!canAnswer || locked || !otherText.trim()"
            :aria-label="t('ask.replies.otherSend')"
            :title="t('ask.replies.otherSend')"
            @click="sendOther"
          />
        </div>
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
  </section>
</template>

<style scoped>
/* 一张卡：和输入框同一种边（--line、--radius-lg），占满消息正文那一栏的宽。里面一行
   一行用发丝线隔开，不再是几张各自描边的小卡。字号只有三档：题目 15（和消息正文
   同档），选项和输入 14，其余（谁问的、编号、说明、推荐、撤回）13。 */
.ask-replies {
  --ask-pad: 16px;
  margin-top: 4px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
/* 聚焦时边只提一档，和输入框一样。 */
.ask-replies:focus-within {
  border-color: var(--faint);
}
.ask-replies:focus-visible {
  outline-offset: -2px;
}
@media (max-width: 599.98px) {
  .ask-replies {
    --ask-pad: 12px;
  }
}
.ask-replies__head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 8px 12px var(--ask-pad);
}
.ask-replies__heading {
  flex: 1 1 auto;
  min-width: 0;
}
.ask-replies__who {
  margin: 0 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.ask-replies__name {
  font-weight: 500;
  color: var(--text);
}
/* 题目是这张卡的标题：消息正文那一档，墨色、加一点重。 */
.ask-replies__question {
  font-size: 15px;
  line-height: var(--lh-15-reading);
  font-weight: 500;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.ask-replies__question :slotted(.im-text) {
  color: var(--ink);
}
/* ✕ 收在页头右上角、卡里面：压到 28px，和页头第一行对齐。触屏上能点的范围
   BaseButton 自己会撑到 44px。 */
.ask-replies__skip {
  flex: none;
  width: 28px;
  height: 28px;
  margin-top: -4px;
}
/* 选项、末行的输入框、答过的那几句都是卡里的一行：上面一条发丝线。 */
.ask-replies__option,
.ask-replies__other,
.ask-replies__row {
  border-top: 1px solid var(--line);
}
.ask-replies__slot {
  display: block;
}
.ask-replies__option {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  column-gap: 8px;
  align-items: start;
  width: 100%;
  min-height: 44px;
  padding: 12px var(--ask-pad);
  font: inherit;
  text-align: left;
  color: var(--text);
  background: none;
  border-right: 0;
  border-bottom: 0;
  border-left: 0;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ask-replies__option:hover:not(:disabled) {
  background: var(--fill);
}
/* 卡是 overflow: hidden，焦点圈画在行内，否则左右两边被裁掉。 */
.ask-replies__option:focus-visible {
  outline-offset: -2px;
}
.ask-replies__num {
  font-size: 13px;
  line-height: var(--lh-14);
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.ask-replies__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.ask-replies__label {
  font-size: 14px;
  line-height: var(--lh-14);
}
/* 选项和说明是模型写的，常带一个 URL 或一长串不断开的词：窄屏上它会撑出卡片。 */
.ask-replies__text,
.ask-replies__explain,
.ask-replies__answer {
  overflow-wrap: anywhere;
}
.ask-replies__text {
  color: var(--ink);
}
/* 「推荐」是跟在选项后面的一个小标签，不另起一行。 */
.ask-replies__tag {
  display: inline-block;
  margin-left: 8px;
  padding: 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  vertical-align: 1px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}
.ask-replies__explain {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
/* 问的不是看的人：选项照样读得到，只是降一档、点不动；页头那句说了在等谁。末行的
   输入框也照这样灰。 */
.ask-replies__option:disabled {
  cursor: default;
}
.ask-replies__option:disabled .ask-replies__text {
  color: var(--muted);
}
.ask-replies__option:disabled .ask-replies__explain,
.ask-replies__option:disabled .ask-replies__tag,
.ask-replies__option:disabled .ask-replies__num {
  color: var(--faint);
}
/* 末行：和选项同一种行，编号那一格空着，字和选项的字对齐。 */
.ask-replies__other {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  column-gap: 8px;
  padding: 8px 8px 8px calc(var(--ask-pad) + 24px);
}
.ask-replies__other-input {
  width: 100%;
  min-width: 0;
  max-height: 160px;
  padding: 4px 0;
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
.ask-replies__other--off .ask-replies__other-input::placeholder {
  color: var(--faint);
}
.ask-replies__other-send {
  width: 28px;
  height: 28px;
}
.ask-replies__answers {
  margin: 0;
  padding: 0;
  list-style: none;
}
.ask-replies__row {
  margin: 0;
  padding: 12px var(--ask-pad);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.ask-replies__closed {
  color: var(--muted);
}
.ask-replies__withdraw {
  margin-left: 8px;
  padding: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: none;
  border: 0;
  text-decoration: underline;
}
.ask-replies__withdraw:hover {
  color: var(--text);
}
/* 触屏上全站的输入框都是 16px（style.css，免得 iOS 聚焦时放大页面），末行的输入框也
   是。卡里和它并排的字跟着到 16：题目、选项、答过的那几句和输入框同一档，卡里仍只有
   两三种字号，而不是 13 / 14 / 15 之外再多一个 16。排在最后：同样的选择器，后写的赢。 */
@media (pointer: coarse) {
  .ask-replies__question,
  .ask-replies__label,
  .ask-replies__row {
    font-size: 16px;
    line-height: var(--lh-15-reading);
  }
  .ask-replies__question :slotted(.im-text) {
    font-size: 16px;
  }
  .ask-replies__num,
  .ask-replies__other-input {
    line-height: var(--lh-15-reading);
  }
}
</style>
