<script setup lang="ts">
/**
 * 一次问了 2–3 道题时的整张卡：顶上一条题签栏，一题一屏，末屏确认。
 *
 * 几道题本来各是一条消息（`block.questions.post_questions`），这里把它们收成一张卡画
 * （分组判据在 `lib/askGroups`）。答案攒在本机，**一次交**：提交只发一条消息，正文
 * 是逐题的答案（`POST /blocks/{id}/answers`）。没交之前随便改、也能跳过不答。
 *
 * 样式照 `AskQuickReplies.vue` 的卡片：同一条外框、发丝线分隔的行、12/13/14/15 四档
 * 字号、原生 input 的单选与多选。题签栏是产品里没有的部件，做得最安静（13px 文字签，
 * 不加底色块、不用主题色）。
 *
 * 没作答也能切题、也能提交——和 Claude Code 的提问卡一致；提交前那一屏把已答的列一遍，
 * 缺的只提醒不拦。
 */
import type { Block } from '../../cx_types'
import type { GroupAnswer } from '../../lib/askGroups'

import { computed, ref } from 'vue'

import { askHeader, askMultiSelect } from '../../lib/askGroups'
import { askAnswers, askClosedByAsker, askOptions } from '../../lib/blockDisplay'
import BaseButton from '../base/BaseButton.vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一组的每一道题，按先后。至少两道。 */
  blocks: Block[]
  /** 问的那一位的显示名；和正上方那一行重了就不给。 */
  asker?: string
  names: Record<string, string>
  /** 这里已经不能回答了：任务关了、频道归档了。 */
  closed?: boolean
  /** 看着这间房的人（自己的 handle）。 */
  viewer?: string
  /** 提交在路上：由面板拿着（`useAskGroupAnswer` 的 busy）。 */
  busy?: boolean
  /** 提交失败时那面横幅。 */
  failure?: string | null
}>()

const emit = defineEmits<{
  /** 把整组答案交上去；请求由面板发，组件不取数据。 */
  (e: 'submit', payload: { questionId: string; answers: Record<string, GroupAnswer> }): void
}>()

type Draft = { options: string[]; note: string }

const questions = computed(() => props.blocks)
const total = computed(() => questions.value.length)
/** 别人答过的题（一道题只收一个人的回答；自己答过的那几条不算，改得动）。 */
const answeredByOthers = computed(() =>
  questions.value.some((q) => askAnswers(q).some((a) => !a.skipped && a.by !== props.viewer))
)
/** 我自己答过了没有——「已提交」看的是这个，不是本地的一个标记。 */
const submitted = computed(() =>
  questions.value.some((q) => askAnswers(q).some((a) => !a.skipped && a.by === props.viewer))
)
const canAnswer = computed(() => {
  if (props.closed || answeredByOthers.value) return false
  return questions.value.some((q) => mayAnswer(q))
})

function mayAnswer(block: Block): boolean {
  // 提问方把这道题收回去了：这一组里它不再等人答（过期的那条还能答）。
  if (askClosedByAsker(block) === 'withdrawn') return false
  const meta = (block.meta ?? {}) as { asked?: string[]; asked_all?: boolean }
  const asked = meta.asked ?? []
  return !!props.viewer && (asked.length === 0 || !!meta.asked_all || asked.includes(props.viewer))
}

/** 服务端已经记下的答案：题上的 `answer_log`。提交成没成看它，不看本地标记。 */
function recordedAnswers(): Draft[] {
  return questions.value.map((q) => {
    const entry = askAnswers(q).find((a) => !a.skipped)
    if (!entry) return { options: [], note: '' }
    // 多选题记的是一串，拆回每一项；单选记的就是那一个选项。都对不上就是他自己写的话。
    const offered = askOptions(q) ?? []
    const picked = entry.text.split(t('ask.group.listSeparator'))
    const known = picked.filter((text) => offered.some((o) => o.text === text))
    if (known.length && known.length === picked.length) return { options: known, note: '' }
    return { options: [], note: entry.text }
  })
}

const editing = ref(false)
/** 现在是不是在答题那一屏：没交过，或者点了「改答案」重开。 */
const answering = computed(() => (!submitted.value || editing.value) && !reviewing.value)
/** 已交、且没在改：这一屏是「答案」，不是「确认」。 */
const showingAnswers = computed(() => submitted.value && !editing.value)
// 每题一个草稿。没交之前它只活在这个组件里，换设备/刷新就没了（v1 的取舍）；
// 「改答案」时用服务端记下的答案把它填回去。
const drafts = ref<Draft[]>(recordedAnswers())
const at = ref(0)
const reviewing = ref(false)

const answeredCount = computed(() => drafts.value.filter((d) => d.options.length > 0 || d.note.trim()).length)
const missing = computed(() => total.value - answeredCount.value)

function draftFor(index: number): Draft {
  return drafts.value[index] ?? { options: [], note: '' }
}
function chosen(index: number, text: string): boolean {
  return draftFor(index).options.includes(text)
}

function pick(index: number, text: string, multi: boolean) {
  if (!canAnswer.value || !answering.value) return
  const draft = draftFor(index)
  if (multi) {
    const at2 = draft.options.indexOf(text)
    if (at2 >= 0) draft.options.splice(at2, 1)
    else draft.options.push(text)
  } else {
    draft.options = draft.options[0] === text ? [] : [text]
  }
  // 选了选项就把自己写的那句清掉：两个只能有一个。
  if (draft.options.length) draft.note = ''
}

function typeNote(index: number, value: string) {
  const draft = draftFor(index)
  draft.note = value
  if (value.trim()) draft.options = []
}

function label(block: Block, index: number): string {
  return askHeader(block) || t('ask.group.questionOf', { n: index + 1, total: total.value })
}

function answeredText(index: number): string {
  const draft = draftFor(index)
  if (draft.options.length) return draft.options.join(t('ask.group.listSeparator'))
  return draft.note.trim()
}

function submit() {
  if (props.busy || !canAnswer.value) return
  const answers: Record<string, GroupAnswer> = {}
  questions.value.forEach((q, i) => {
    const draft = draftFor(i)
    if (draft.options.length) answers[q.id] = { options: [...draft.options] }
    else if (draft.note.trim()) answers[q.id] = { note: draft.note.trim() }
  })
  // 意图发上去，请求由面板发（`composables/useAskGroupAnswer`）——组件不取数据。
  emit('submit', { questionId: questions.value[0]!.id, answers })
  reviewing.value = false
  editing.value = false
}

function reopen() {
  // 把服务端记下的答案填回草稿，改的是同一件事的下一版；再交一次整组覆盖。
  drafts.value = recordedAnswers()
  editing.value = true
  reviewing.value = false
  at.value = 0
}
</script>

<template>
  <section class="ask-group" :class="{ 'ask-group--closed': !canAnswer }">
    <!-- 题签栏：每题一枚，答过的打勾；点一下切题，不选也能走。
         「提交」不跟着题签一起滚——窄屏上题签能横向滑，它一直摆在右边。 -->
    <div class="ask-group__tabsrow">
      <div class="ask-group__tabs" role="tablist" :aria-label="t('ask.group.review')">
        <button
          v-for="(q, i) in questions"
          :key="q.id"
          type="button"
          role="tab"
          class="ask-group__tab"
          :aria-selected="answering && at === i"
          :disabled="!answering"
          :title="q.content"
          @click="(at = i), (reviewing = false)"
        >
          <span v-if="askHeader(q) || answeredText(i)" class="ask-group__tab-no" aria-hidden="true">{{
            answeredText(i) ? '✓' : i + 1
          }}</span>
          <span class="ask-group__tab-label">{{ askHeader(q) || i + 1 }}</span>
        </button>
      </div>
      <button
        type="button"
        class="ask-group__tab ask-group__tab--submit"
        :disabled="!answering || !canAnswer"
        @click="reviewing = true"
      >
        {{ answering ? t('ask.group.submit') : t('ask.group.submitted') }}
      </button>
    </div>

    <!-- 一题一屏 -->
    <template v-if="answering">
      <div class="ask-group__slab">
        <p class="ask-group__step">{{ label(questions[at]!, at) }}</p>
        <p class="ask-group__question">{{ questions[at]!.content }}</p>
        <p v-if="askMultiSelect(questions[at]!)" class="ask-group__hint">{{ t('ask.group.multiHint') }}</p>

        <ul class="ask-group__opts">
          <li v-for="option in askOptions(questions[at]!) ?? []" :key="option.text" class="ask-group__opt">
            <label :class="{ 'is-picked': chosen(at, option.text) }">
              <input
                :type="askMultiSelect(questions[at]!) ? 'checkbox' : 'radio'"
                :name="`ask-group-${questions[at]!.id}`"
                autocomplete="off"
                :checked="chosen(at, option.text)"
                :disabled="!canAnswer"
                @change="pick(at, option.text, askMultiSelect(questions[at]!))"
              />
              <span>
                <span class="ask-group__lb">{{ option.text }}</span>
                <span v-if="option.explain" class="ask-group__ex">{{ option.explain }}</span>
              </span>
            </label>
          </li>
          <li class="ask-group__other">
            <input
              :value="draftFor(at).note"
              type="text"
              autocomplete="off"
              :placeholder="t('ask.group.otherLabel')"
              :aria-label="t('ask.group.otherLabel')"
              :disabled="!canAnswer"
              @input="typeNote(at, ($event.target as HTMLInputElement).value)"
            />
          </li>
        </ul>
      </div>

      <div class="ask-group__bar">
        <BaseButton kind="secondary" size="sm" :disabled="at === 0" @click="at -= 1">{{
          t('ask.group.previous')
        }}</BaseButton>
        <span class="ask-group__spacer" />
        <BaseButton v-if="at < total - 1" kind="primary" size="sm" @click="at += 1">{{
          t('ask.group.next')
        }}</BaseButton>
        <BaseButton v-else kind="primary" size="sm" @click="reviewing = true">{{ t('ask.group.submit') }}</BaseButton>
      </div>
    </template>

    <!-- 确认屏 / 已提交：按题列出答案，读得清 -->
    <template v-else>
      <div class="ask-group__review">
        <h4>
          {{
            showingAnswers
              ? asker
                ? t('ask.group.title', { asker, n: total })
                : t('ask.group.titleBare', { n: total })
              : t('ask.group.review')
          }}
        </h4>
        <p v-if="!showingAnswers" class="ask-group__notice" :class="missing ? 'is-warn' : 'is-ok'">
          {{ missing ? t('ask.group.reviewMissing', { n: missing }) : t('ask.group.reviewAllAnswered', { total }) }}
        </p>

        <ul class="ask-group__recap">
          <li v-for="(q, i) in questions" :key="q.id" :class="{ 'is-missing': !answeredText(i) }">
            <span class="ask-group__recap-no">{{ i + 1 }}</span>
            <span>
              <span class="ask-group__recap-q">{{ q.content }}</span>
              <span class="ask-group__recap-a">{{ answeredText(i) || t('ask.group.unanswered') }}</span>
            </span>
          </li>
        </ul>

        <div class="ask-group__bar">
          <template v-if="showingAnswers">
            <BaseButton kind="secondary" size="sm" @click="reopen">{{ t('ask.group.edit') }}</BaseButton>
            <span class="ask-group__spacer" />
            <span class="ask-group__note">{{ t('ask.group.editNote') }}</span>
          </template>
          <template v-else>
            <BaseButton kind="secondary" size="sm" @click="reviewing = false">{{ t('ask.group.back') }}</BaseButton>
            <span class="ask-group__spacer" />
            <BaseButton kind="primary" size="sm" :disabled="busy || !canAnswer" @click="submit">
              {{ t('ask.group.submit') }}
            </BaseButton>
          </template>
        </div>
        <p v-if="failure" class="ask-group__failure">{{ failure }}</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 和 AskQuickReplies 同一条外框、同一个圆角：它是同一张卡，只是装了几道题。 */
.ask-group {
  --ask-pad: 16px;
  margin-top: 4px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.ask-group:focus-within {
  border-color: var(--faint);
}
@media (max-width: 599.98px) {
  .ask-group {
    --ask-pad: 12px;
  }
}

/* 题签栏：产品里没有这个部件，所以最安静——13px 文字、不加底色块、不用主题色。 */
.ask-group__tabsrow {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--line);
}
.ask-group__tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 0 6px 8px;
  overflow-x: auto;
  scrollbar-width: none;
  min-width: 0;
  flex: 1;
  /* 题签排不下时右边渐隐，看得出还能横滑——最后一枚被切掉一半是条线索，不是提示。 */
  /* currentColor 只为它的不透明度：遮罩不看颜色，深色主题下同样成立。 */
  mask-image: linear-gradient(to right, currentColor calc(100% - 20px), transparent);
}
.ask-group__tabs::-webkit-scrollbar {
  display: none;
}
.ask-group__tab {
  appearance: none;
  border: 0;
  background: none;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ask-group__tab:hover:not(:disabled) {
  background: var(--fill);
}
.ask-group__tab:focus-visible {
  outline-offset: -2px;
}
.ask-group__tab[aria-selected='true'] {
  color: var(--ink);
  font-weight: 500;
}
.ask-group__tab:disabled {
  cursor: default;
  color: var(--faint);
}
.ask-group__tab-no {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  min-width: 9px;
  text-align: center;
}
.ask-group__tab[aria-selected='true'] .ask-group__tab-no {
  color: var(--ink);
}
.ask-group__tab-label {
  overflow-wrap: anywhere;
}
.ask-group__tab--submit {
  flex: none;
  color: var(--ink);
  font-weight: 500;
  padding: 6px 12px;
}
.ask-group__tab--submit:disabled {
  color: var(--faint);
  font-weight: 400;
}

.ask-group__slab {
  padding: 16px;
}
.ask-group__step {
  margin: 0 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.ask-group__question {
  margin: 0;
  font-size: 15px;
  line-height: var(--lh-15-reading);
  font-weight: 500;
  color: var(--ink);
  text-wrap: balance;
}
.ask-group__hint {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 选项：卡里一行一行，上面一条发丝线——和真卡片一样，不是各自描边的小块。 */
.ask-group__opts {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}
.ask-group__opt label {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  column-gap: 8px;
  align-items: start;
  min-height: 44px;
  padding: 12px var(--ask-pad);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  cursor: pointer;
  border-bottom: 1px solid var(--line);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ask-group__opt label:hover {
  background: var(--fill);
}
.ask-group__opt label.is-picked {
  background: var(--line-2);
}
.ask-group__opt input {
  margin: 3px 0 0;
  accent-color: var(--accent);
  cursor: pointer;
}
.ask-group__lb {
  display: block;
}
.ask-group__opt label.is-picked .ask-group__lb {
  color: var(--ink);
  font-weight: 500;
}
.ask-group__ex {
  display: block;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  margin-top: 2px;
}
.ask-group__other {
  padding: 8px var(--ask-pad) 8px calc(var(--ask-pad) + 24px);
}
.ask-group__other input {
  width: 100%;
  min-width: 0;
  padding: 6px 0;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  background: transparent;
  border: 0;
  outline: none;
}
.ask-group__other input::placeholder {
  color: var(--faint);
}

.ask-group__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-top: 1px solid var(--line);
}
.ask-group__spacer {
  flex: 1;
}

.ask-group__review {
  padding: 16px;
}
.ask-group__review h4 {
  margin: 0 0 10px;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 500;
  color: var(--ink);
}
/* 提示条：左边一条色带，不加符号。 */
.ask-group__notice {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  border-left: 3px solid var(--line-2);
  background: var(--fill);
  color: var(--muted);
}
.ask-group__notice.is-warn {
  border-left-color: var(--warn);
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.ask-group__notice.is-ok {
  border-left-color: var(--ok);
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.ask-group__recap {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}
.ask-group__recap li {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.ask-group__recap-no {
  font-size: 13px;
  line-height: var(--lh-14);
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.ask-group__recap-q {
  display: block;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  margin-bottom: 3px;
}
.ask-group__recap-a {
  display: block;
  font-size: 15px;
  line-height: var(--lh-15-reading);
  color: var(--ink);
  font-weight: 500;
  overflow-wrap: anywhere;
}
.ask-group__recap li.is-missing .ask-group__recap-a {
  color: var(--faint);
  font-weight: 400;
}
.ask-group__failure {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--danger-ink);
}
.ask-group__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
