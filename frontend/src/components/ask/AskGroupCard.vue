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

import { computed, ref } from 'vue'

import { answerGroup } from '../../api/blocks'
import { askHeader, askMultiSelect } from '../../lib/askGroups'
import { askAnswers, askOptions } from '../../lib/blockDisplay'
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
}>()

const emit = defineEmits<{ (e: 'answered', blocks: Block[]): void }>()

type Draft = { options: string[]; note: string }

const questions = computed(() => props.blocks)
const total = computed(() => questions.value.length)
/** 已经有人答过的那一道（只有一个回答，谁先答算谁的）。 */
const answeredElsewhere = computed(() => questions.value.some((q) => askAnswers(q).some((a) => !a.skipped)))
const canAnswer = computed(() => {
  if (props.closed || answeredElsewhere.value) return false
  return questions.value.some((q) => mayAnswer(q))
})

function mayAnswer(block: Block): boolean {
  const meta = (block.meta ?? {}) as { asked?: string[]; asked_all?: boolean }
  const asked = meta.asked ?? []
  return !!props.viewer && (asked.length === 0 || !!meta.asked_all || asked.includes(props.viewer))
}

// 每题一个草稿。没交之前它只活在这个组件里，换设备/刷新就没了（v1 的取舍）。
const drafts = ref<Draft[]>(questions.value.map(() => ({ options: [], note: '' })))
const at = ref(0)
const reviewing = ref(false)
const submitted = ref(false)
const busy = ref(false)
const failure = ref<string | null>(null)

const answeredCount = computed(() => drafts.value.filter((d) => d.options.length > 0 || d.note.trim()).length)
const missing = computed(() => total.value - answeredCount.value)

function draftFor(index: number): Draft {
  return drafts.value[index] ?? { options: [], note: '' }
}
function chosen(index: number, text: string): boolean {
  return draftFor(index).options.includes(text)
}

function pick(index: number, text: string, multi: boolean) {
  if (!canAnswer.value || submitted.value) return
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

async function submit() {
  if (busy.value || !canAnswer.value) return
  busy.value = true
  failure.value = null
  try {
    const answers: Record<string, { options?: string[]; note?: string }> = {}
    questions.value.forEach((q, i) => {
      const draft = draftFor(i)
      if (draft.options.length) answers[q.id] = { options: [...draft.options] }
      else if (draft.note.trim()) answers[q.id] = { note: draft.note.trim() }
    })
    const result = await answerGroup(questions.value[0]!.id, answers)
    submitted.value = true
    reviewing.value = false
    emit('answered', result.questions)
  } catch (error) {
    failure.value = error instanceof Error ? error.message : t('ask.group.failed')
  } finally {
    busy.value = false
  }
}

function reopen() {
  submitted.value = false
  reviewing.value = false
  at.value = 0
  // 答案留着，重新打开只是为了改；再次提交会整组覆盖。
}
</script>

<template>
  <section class="ask-group" :class="{ 'ask-group--closed': !canAnswer }">
    <!-- 题签栏：每题一枚，答过的打勾；箭头键在题之间切，不选也能走。 -->
    <div class="ask-group__tabs" role="tablist" :aria-label="t('ask.group.review')">
      <button
        v-for="(q, i) in questions"
        :key="q.id"
        type="button"
        role="tab"
        class="ask-group__tab"
        :aria-selected="!reviewing && at === i"
        :disabled="submitted"
        :title="q.content"
        @click="(at = i), (reviewing = false)"
      >
        <span class="ask-group__tab-no" aria-hidden="true">{{ answeredText(i) ? '✓' : i + 1 }}</span>
        <span class="ask-group__tab-label">{{ askHeader(q) || i + 1 }}</span>
      </button>
      <button
        type="button"
        class="ask-group__tab ask-group__tab--submit"
        :disabled="submitted || !canAnswer"
        @click="reviewing = true"
      >
        {{ submitted ? t('ask.group.submitted') : t('ask.group.submit') }}
      </button>
    </div>

    <!-- 一题一屏 -->
    <template v-if="!submitted && !reviewing">
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
            submitted
              ? asker
                ? t('ask.group.title', { asker, n: total })
                : t('ask.group.titleBare', { n: total })
              : t('ask.group.review')
          }}
        </h4>
        <p v-if="!submitted" class="ask-group__notice" :class="missing ? 'is-warn' : 'is-ok'">
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
          <template v-if="submitted">
            <BaseButton kind="secondary" size="sm" @click="reopen">{{ t('ask.group.edit') }}</BaseButton>
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
.ask-group__tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  overflow-x: auto;
  scrollbar-width: none;
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
  margin-left: auto;
  color: var(--ink);
  font-weight: 500;
  padding-right: 12px;
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
</style>
