<script setup lang="ts">
/**
 * 提问流程 —— **提案组件，尚未接进产品。**
 *
 * `AskCard.vue` 是其中一格（一道题）。这一份是**完整流程**：好几件待拍板的事摆在一起
 * 能切换、看得出进度，每题的选项带解释、备注跟着这道题走，写了一半知道还没交，稍后
 * 处理找得回来，交得上去也交得失败、失败能重试，答完留回执、之后能改，刷新回来草稿
 * 和没交的都还在。
 *
 * 形状不是我编的，照的是 Codex 自己那份工具定义和 TUI 源码（`openai/codex`，2026-09-30
 * 读的 `codex-rs/protocol/src/request_user_input.rs`、`codex-rs/core/src/tools/handlers/
 * request_user_input*.rs`、`codex-rs/tui/src/bottom_pane/request_user_input/mod.rs`）：
 *   - 一次给 1-3 道自足的题，一起摆出来；每题一个短标签（≤12 字）。
 *   - 选项 2-3 个互斥，**推荐的排第一**；标签 1-5 个字，下面一句「选了会怎样」。
 *   - **「以上都不是」是界面自动加的**，不要让提问方自己写进选项里。
 *   - 自由输入是界面自动给的，空着也答得了；纯问答题可以一个选项都没有。
 *   - 每题自己存一份备注草稿，和一个「是否已经明确交了」的标记。
 *   - 回车是「下一道题 / 最后一道题就全部提交」，不是点一下选项就发出去。
 *   - 带着没答的题提交时，先问一句「有 N 题没答，照交还是回去补」。
 *   - 打断时记「有 N 题没答」，不假装答上了。
 *
 * **只加不改**：产品里现有的动作一个都没动。接不接进 `RoomMessage`、怎么接，等验收人
 * 看过再说。**演示组件不等于产品已实现。**
 *
 * 样式全走 `style.css` 的令牌，深色是白拿的（这里没有一个 `--xxx-dark`）。文案先写死
 * 中文，接进产品时再迁进 `zh-CN` / `en` 两张表。
 */
import type { AskFlowProps, AskOption, AskQuestion, QState } from './askFlowState'

import { computed, ref, watch } from 'vue'
export type { AskFlowProps, AskOption, AskQuestion } from './askFlowState'
import { restoreFlowDraft, saveFlowDraft } from './askFlowState'

const props = withDefaults(defineProps<AskFlowProps>(), {
  answeredBy: '王长鑫',
  initialIndex: 0,
  initialPicked: () => ({}),
  initialNotes: () => ({}),
  initialFailed: () => [],
  initialAnswered: () => ({}),
  initialDeferred: () => [],
  initialConfirm: false,
  persistKey: '',
})

const emit = defineEmits<{
  submit: [payload: { id: string; option: string; note: string }]
  defer: [payload: { id: string }]
  correct: [payload: { id: string; option: string; note: string; was: string }]
}>()

const state = ref<Record<string, QState>>({})
for (const q of props.questions) {
  state.value[q.id] = {
    picked: props.initialPicked[q.id] ?? null,
    note: props.initialNotes[q.id] ?? '',
    committed: Boolean(props.initialAnswered[q.id]),
    deferred: props.initialDeferred.includes(q.id),
    failed: props.initialFailed.includes(q.id),
    submitted: props.initialAnswered[q.id] ?? null,
    was: null,
  }
}

const currentIdx = ref(Math.min(Math.max(props.initialIndex, 0), Math.max(props.questions.length - 1, 0)))

restoreFlowDraft(props.persistKey, props.questions, state.value)
watch(state, () => saveFlowDraft(props.persistKey, props.questions, state.value), { deep: true })
const current = computed(() => props.questions[currentIdx.value])
const cur = computed(() => state.value[current.value.id])

const total = computed(() => props.questions.length)
const answeredCount = computed(() => props.questions.filter((q) => state.value[q.id]?.submitted).length)
const deferredCount = computed(
  () => props.questions.filter((q) => state.value[q.id]?.deferred && !state.value[q.id]?.submitted).length
)
const failedCount = computed(() => props.questions.filter((q) => state.value[q.id]?.failed).length)

/**
 * 「以上都不是」是**界面自动加的**（Codex: the client will add a free-form "Other" option
 * automatically），所以提问方给的选项里不该有它。它的作用是给「都不对」一条正路：选了
 * 它就等于说「这几个都不对」，话写在备注里，带着理由照样能交、能继续干。
 */
const OTHER_LABEL = '以上都不是'
const OTHER_DESC = '都不对的话，把你要的写在下面备注里'

function optionsOf(q: AskQuestion): AskOption[] {
  return [...q.options, { label: OTHER_LABEL, description: OTHER_DESC }]
}

/** 写了一半还没交 —— 界面要直说，别让人以为已经答上了。 */
const hasDraft = computed(() => {
  const s = cur.value
  return !s.committed && (s.picked !== null || s.note.trim() !== '')
})

const isOtherPicked = computed(() => cur.value.picked !== null && cur.value.picked === current.value.options.length)

/** 这道题算不算「答了」：选了一项，或者写了话。纯问答题靠写话。 */
function isAnswered(id: string, s: QState): boolean {
  if (s.submitted) return true
  return s.picked !== null || s.note.trim() !== ''
}

const unansweredCount = computed(() => props.questions.filter((q) => !isAnswered(q.id, state.value[q.id])).length)

/** 提交前若有没答的题，先问一句 —— Codex 的「Submit with N unanswered questions?」。 */
const confirmUnanswered = ref(props.initialConfirm)

/**
 * 交上去那一下正在走 —— **这段时间不许再交第二次**。
 *
 * 这是「防重复执行」的最小形状：连点两次不会再打两次服务，慢的那一次回来也晚了。
 */
const submitting = ref(false)

function pick(i: number) {
  cur.value.picked = i
}

/**
 * 提交这道题。
 *
 * **选择不自动等于提交**：点选只改 `picked`；要走到这里、而且服务回了成功，才算
 * `committed`。服务没回来之前界面不许当成答上了 —— 失败时一个字都不能丢。
 */
async function submitCurrent() {
  const s = cur.value
  const opts = optionsOf(current.value)
  // 什么都没选、也没写话，交不出东西：纯问答题写话就够，有选项的题至少选一项。
  if (s.picked === null && s.note.trim() === '') return
  if (unansweredCount.value > 0 && !confirmUnanswered.value) {
    confirmUnanswered.value = true
    return
  }
  confirmUnanswered.value = false
  if (submitting.value) return
  const id = current.value.id
  const option = s.picked === null ? '' : opts[s.picked].label
  const note = s.note.trim()
  submitting.value = true
  try {
    if (props.submitAnswer) await props.submitAnswer({ id, option, note })
  } catch {
    // 服务拒了：不记任何账，选的和写的原样留着，重试时原样再交一次。
    s.failed = true
    submitting.value = false
    return
  }
  submitting.value = false
  s.failed = false
  const was = s.submitted
  if (was) s.was = { option: was.option, note: was.note }
  s.submitted = {
    option,
    note,
    by: props.answeredBy,
    at: '刚刚',
  }
  s.committed = true
  s.deferred = false
  if (was) {
    emit('correct', { id, option, note, was: was.option })
  } else {
    emit('submit', { id, option, note })
  }
}

/**
 * 重试就是把**已经确认过的那一批**再交一次 —— 成不成功由服务说了算，组件不自己先说成了。
 *
 * 「还有没答的，照样交吗」上一次已经答过了，重试不该再问一遍：那是同一次决定的重发，
 * 不是一次新的提交。每次重试都弹一遍确认，就是让人重复选已经定过的事。
 */
function retry() {
  confirmUnanswered.value = true
  submitCurrent()
}

function deferCurrent() {
  cur.value.deferred = true
  confirmUnanswered.value = false
  emit('defer', { id: current.value.id })
}

/** 改答案：留着上一版，不抹掉。更正是新的一版，不是假装没答过。 */
function correctCurrent() {
  const s = cur.value
  if (s.submitted) s.was = { option: s.submitted.option, note: s.submitted.note }
  s.submitted = null
  s.committed = false
  s.failed = false
  s.deferred = false
}

function reopenCurrent() {
  cur.value.deferred = false
}

function goTo(i: number) {
  currentIdx.value = i
  confirmUnanswered.value = false
}

/**
 * 键盘优先：上下键 / j k 选，数字键直接挑，回车提交，Esc 稍后。
 *
 * **两处必须放行，否则会把中文输入和打字抢走**：
 *   - 输入法组字中（`isComposing`，或 `keyCode === 229` 这个各家输入法都在用的旧值）
 *     —— 这时候按 j 想打「就」，不能被当成方向键。
 *   - 焦点在输入框里 —— 打字就是打字，j / k / 数字都不该被抢。备注框的回车提交是它
 *     自己的 `@keydown.enter`，不走这里。
 */
/**
 * 备注框里的回车＝提交（Codex 的 composer submit binding）。输入法组字中的回车是
 * 「选定候选词」，不是提交 —— 这时候不放行会把半句话交出去。
 */
function onNoteEnter(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  submitCurrent()
}

function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  const t = e.target as HTMLElement | null
  const typing = t !== null && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
  if (typing) return

  const s = cur.value
  const opts = optionsOf(current.value)
  if (e.key === 'ArrowUp' || e.key === 'k') {
    e.preventDefault()
    if (s.submitted) return
    s.picked = s.picked === null ? 0 : (s.picked - 1 + opts.length) % opts.length
  } else if (e.key === 'ArrowDown' || e.key === 'j') {
    e.preventDefault()
    if (s.submitted) return
    s.picked = s.picked === null ? 0 : (s.picked + 1) % opts.length
  } else if (/^[1-9]$/.test(e.key)) {
    const i = Number(e.key) - 1
    if (i < opts.length && !s.submitted) {
      e.preventDefault()
      s.picked = i
    }
  } else if (e.key === 'Enter') {
    e.preventDefault()
    submitCurrent()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    if (!s.submitted) deferCurrent()
  }
}
</script>

<template>
  <section class="flow" tabindex="0" @keydown="onKey">
    <!-- 总进度：好几件待拍板的事摆在一起，一眼看出一共几件、答了几件。 -->
    <header class="flow__head">
      <div class="flow__title-wrap">
        <h2 class="flow__title">等你拍板</h2>
        <span class="flow__badge">{{ total }} 件</span>
        <span v-if="deferredCount" class="flow__badge flow__badge--soft">{{ deferredCount }} 件先放着</span>
        <span v-if="failedCount" class="flow__badge flow__badge--warn">{{ failedCount }} 件没交上</span>
      </div>
      <div class="flow__progress" role="status">
        <span class="flow__progress-text">{{ answeredCount }}/{{ total }} 已答</span>
        <span class="flow__bar" aria-hidden="true">
          <span class="flow__bar-fill" :style="{ width: `${(answeredCount / total) * 100}%` }"></span>
        </span>
      </div>
    </header>

    <!-- 多题切换：每题一个短标签（Codex: header ≤12 字）。答过的标出来，放着的也标出来。 -->
    <nav class="flow__tabs" aria-label="待拍板的事">
      <button
        v-for="(q, i) in questions"
        :key="q.id"
        type="button"
        class="flow__tab"
        :class="{
          'flow__tab--on': i === currentIdx,
          'flow__tab--done': state[q.id]?.submitted && !state[q.id]?.was,
          'flow__tab--deferred': state[q.id]?.deferred && !state[q.id]?.submitted,
          'flow__tab--failed': state[q.id]?.failed,
        }"
        @click="goTo(i)"
      >
        <span class="flow__tab-no" aria-hidden="true">{{ i + 1 }}</span>
        <span class="flow__tab-label">{{ q.header }}</span>
        <span v-if="state[q.id]?.submitted" class="flow__tab-mark" aria-label="已答">✓</span>
        <span v-else-if="state[q.id]?.failed" class="flow__tab-mark flow__tab-mark--warn" aria-label="没交上">!</span>
        <span v-else-if="state[q.id]?.deferred" class="flow__tab-mark" aria-label="先放着">·</span>
      </button>
    </nav>

    <!-- 当前这题 -->
    <article class="ask" :class="{ 'ask--answered': cur.submitted, 'ask--deferred': cur.deferred && !cur.submitted }">
      <header class="ask__status">
        <span class="ask__dot" aria-hidden="true"></span>
        <span class="ask__status-text">
          {{ cur.submitted ? '已回答' : cur.deferred ? '你先放着' : cur.failed ? '没交上' : '等你回答' }}
        </span>
        <span v-if="current.age && !cur.submitted" class="ask__age">{{ current.age }}</span>
        <!-- 未提交提示：写了没交，直说。 -->
        <span v-if="hasDraft" class="ask__draft-flag">草稿没交</span>
      </header>

      <h3 class="ask__title">{{ current.question }}</h3>

      <!-- 已答回执。更正语义：上一版留着，不假装没答过。 -->
      <div v-if="cur.submitted" class="ask__receipt">
        <p class="ask__receipt-main">
          <span class="ask__ok" aria-hidden="true">✓</span>
          <strong>{{ cur.submitted.by }}</strong>
          <template v-if="cur.submitted.option">选了「{{ cur.submitted.option }}」</template>
          <template v-else>写了话</template>
        </p>
        <p v-if="cur.submitted.note" class="ask__receipt-note">捎了一句：{{ cur.submitted.note }}</p>
        <p v-if="cur.was" class="ask__receipt-was">
          原来答的是「{{ cur.was.option }}」<template v-if="cur.was.note">（{{ cur.was.note }}）</template>，已经改了。
        </p>
        <p class="ask__receipt-follow">已交给芝士，它下一轮会接着往下做。</p>
        <button type="button" class="ask__ghost" @click="correctCurrent">改答案</button>
      </div>

      <!-- 稍后处理：放着不等于忘了，回头找得回来。 -->
      <div v-else-if="cur.deferred" class="ask__deferred">
        <p class="ask__deferred-text">先放着了。它还在「待拍板」里等你，随时回来答。</p>
        <button type="button" class="ask__ghost" @click="reopenCurrent">现在答</button>
      </div>

      <template v-else>
        <!-- 选项：点一下只是选中，不发出去。每项下面一句「选了会怎样」。 -->
        <ul class="ask__options">
          <li v-for="(opt, i) in optionsOf(current)" :key="opt.label">
            <button
              type="button"
              class="ask__option"
              :class="{ 'ask__option--on': cur.picked === i, 'ask__option--other': i === current.options.length }"
              :aria-pressed="cur.picked === i"
              @click="pick(i)"
            >
              <span class="ask__radio" aria-hidden="true"></span>
              <span class="ask__option-body">
                <span class="ask__option-label">
                  {{ opt.label }}<span v-if="i === 0 && current.options.length" class="ask__reco">推荐</span>
                </span>
                <span v-if="opt.description" class="ask__option-desc">{{ opt.description }}</span>
              </span>
              <span class="ask__key" aria-hidden="true">{{ i + 1 }}</span>
            </button>
          </li>
        </ul>

        <!-- 自由输入：跟着这道题走。选哪一项都能带一句，也可以不带。 -->
        <label class="ask__note-wrap">
          <span class="ask__note-label">备注（可不写）</span>
          <input
            v-model="cur.note"
            autocomplete="off"
            class="ask__note"
            type="text"
            placeholder="或者写一句别的——选项都不合适时，这里说你真正想要的"
            @keydown.enter.prevent="onNoteEnter"
          />
        </label>

        <!-- 提交失败要看得见，并且能重试；不能假装答上了。 -->
        <div v-if="cur.failed" class="ask__fail" role="alert">
          <p class="ask__fail-text"><strong>没答上。</strong>网络断了，你的选择和备注都还在这儿。</p>
          <button type="button" class="ask__ghost" @click="retry">再交一次</button>
        </div>

        <!-- 带着没答的题提交，先问一句。 -->
        <div v-if="confirmUnanswered" class="ask__confirm" role="alertdialog">
          <p class="ask__confirm-text">
            还有 <strong>{{ unansweredCount }}</strong> 件没答，照样交吗？
          </p>
          <div class="ask__confirm-actions">
            <button type="button" class="ask__ghost" @click="confirmUnanswered = false">回去补</button>
            <button type="button" class="ask__primary" @click="submitCurrent">照样交</button>
          </div>
        </div>

        <footer class="ask__actions">
          <!-- 主操作，这一组里唯一的琥珀（设计规范 §1.6）。 -->
          <button
            type="button"
            class="ask__primary"
            :disabled="cur.picked === null && !cur.note.trim()"
            @click="submitCurrent"
          >
            提交回答
          </button>
          <button type="button" class="ask__later" @click="deferCurrent">稍后处理</button>
          <span class="ask__hint">
            {{ cur.picked === null && !cur.note.trim() ? '先选一项，或者写一句' : '回车＝提交，Esc＝稍后' }}
          </span>
        </footer>
      </template>
    </article>

    <!-- 已答清单：Codex 的回执形状是「Questions N/M answered」+ 每题一行答案。 -->
    <section class="flow__echo">
      <h3 class="flow__echo-title">
        已答 <span class="flow__echo-count">{{ answeredCount }}/{{ total }}</span>
      </h3>
      <ul class="flow__echo-list">
        <li v-for="q in questions" :key="q.id" class="flow__echo-item">
          <span class="flow__echo-q">{{ q.header }}</span>
          <span v-if="state[q.id]?.submitted" class="flow__echo-a">
            {{ state[q.id].submitted?.option || '（写了话）'
            }}<template v-if="state[q.id].submitted?.note"> · {{ state[q.id].submitted?.note }}</template>
          </span>
          <span v-else-if="state[q.id]?.deferred" class="flow__echo-a flow__echo-a--soft">（先放着）</span>
          <span v-else class="flow__echo-a flow__echo-a--soft">（未答）</span>
        </li>
      </ul>
    </section>

    <p class="flow__persist">草稿和没交的回答存在这台设备上，刷新、断线重连回来都还在，不会当成已经答了。</p>
  </section>
</template>

<style scoped>
/* ── 流程外壳 ─────────────────────────────────────────── */
.flow {
  max-width: 520px;
  padding: 16px;
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  outline: none;
}
.flow:focus-visible {
  border-color: var(--muted);
}

.flow__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.flow__title-wrap {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.flow__title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}
.flow__badge {
  padding: 1px 7px;
  color: var(--muted);
  font-size: 11px;
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: 999px;
}
.flow__badge--soft {
  color: var(--faint);
}
.flow__badge--warn {
  color: var(--ink);
  background: var(--line-2);
}

.flow__progress {
  display: flex;
  align-items: center;
  gap: 7px;
}
.flow__progress-text {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.flow__bar {
  display: block;
  width: 62px;
  height: 4px;
  overflow: hidden;
  background: var(--line-2);
  border-radius: 999px;
}
.flow__bar-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
  transition: width var(--dur-quick) var(--ease-standard);
}

/* ── 题与题之间切换 ────────────────────────────────────── */
.flow__tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  overflow-x: auto;
}
.flow__tab {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 10px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 999px;
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
.flow__tab:hover {
  background: var(--fill);
}
.flow__tab--on {
  color: var(--ink);
  background: var(--fill);
  border-color: var(--muted);
}
.flow__tab--done {
  color: var(--faint);
}
.flow__tab--deferred {
  color: var(--faint);
  border-style: dashed;
}
.flow__tab--failed {
  color: var(--ink);
}
.flow__tab-no {
  color: var(--faint);
  font-size: 10px;
}
.flow__tab-mark {
  color: var(--ok);
  font-size: 11px;
  font-weight: 700;
}
.flow__tab-mark--warn {
  color: var(--ink);
}

/* ── 一道题 ───────────────────────────────────────────── */
.ask {
  padding: 14px 16px 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.ask--answered,
.ask--deferred {
  background: var(--fill);
}

.ask__status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--faint);
}
.ask__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
.ask--answered .ask__dot,
.ask--deferred .ask__dot {
  background: var(--faint);
}
.ask__age {
  margin-left: auto;
}
/* 未提交提示：写了没交，直接写在脸上。 */
.ask__draft-flag {
  margin-left: auto;
  padding: 1px 7px;
  color: var(--ink);
  font-size: 11px;
  background: var(--line-2);
  border-radius: 999px;
}
.ask__age + .ask__draft-flag {
  margin-left: 6px;
}

.ask__title {
  margin: 0 0 10px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.45;
}

/* ── 选项 ─────────────────────────────────────────────── */
.ask__options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0 0 10px;
  padding: 0;
  list-style: none;
}
.ask__option {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 9px 12px;
  color: var(--ink);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
.ask__option:hover {
  border-color: var(--faint);
  background: var(--fill);
}
/* 选中靠描边和底色说，不上琥珀：琥珀留给唯一那颗「提交回答」。 */
.ask__option--on {
  background: var(--line-2);
  border-color: var(--muted);
}
.ask__option--other {
  border-style: dashed;
}
.ask__radio {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border: 1.5px solid var(--faint);
  border-radius: 50%;
}
.ask__option--on .ask__radio {
  border-color: var(--ink);
  border-width: 4.5px;
}
.ask__option-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ask__option-label {
  font-weight: 500;
  line-height: 1.45;
}
.ask__reco {
  margin-left: 6px;
  padding: 0 5px;
  color: var(--faint);
  font-size: 10px;
  font-weight: 400;
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: 999px;
}
/* 选项解释：一句「选了会怎样」，先看后果再点。 */
.ask__option-desc {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
/* 数字键位提示：键盘优先。 */
.ask__key {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin-top: 1px;
  color: var(--faint);
  font-size: 10px;
  line-height: 15px;
  text-align: center;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

/* ── 自由输入 ──────────────────────────────────────────── */
.ask__note-wrap {
  display: block;
  margin-bottom: 10px;
}
.ask__note-label {
  display: block;
  margin-bottom: 4px;
  color: var(--faint);
  font-size: 11px;
}
.ask__note {
  width: 100%;
  padding: 8px 11px;
  color: var(--ink);
  font-size: 13px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.ask__note::placeholder {
  color: var(--faint);
}
.ask__note:focus {
  border-color: var(--muted);
  outline: none;
}

/* ── 失败与重试 ────────────────────────────────────────── */
.ask__fail {
  margin: 0 0 10px;
  padding: 9px 11px;
  background: var(--fill);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink);
  border-radius: var(--radius-md);
}
.ask__fail-text {
  margin: 0 0 8px;
  color: var(--ink);
  font-size: 12px;
  line-height: 1.55;
}

/* ── 未答确认 ──────────────────────────────────────────── */
.ask__confirm {
  margin: 0 0 10px;
  padding: 10px 12px;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.ask__confirm-text {
  margin: 0 0 8px;
  color: var(--ink);
  font-size: 12px;
  line-height: 1.55;
}
.ask__confirm-actions {
  display: flex;
  gap: 8px;
}

/* ── 动作行 ────────────────────────────────────────────── */
.ask__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.ask__primary {
  height: 34px;
  padding: 0 15px;
  color: rgb(var(--v-theme-on-primary));
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  background: var(--accent);
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}
.ask__primary:hover:not(:disabled) {
  background: var(--accent-press);
}
.ask__primary:disabled {
  cursor: default;
  opacity: 0.45;
}
.ask__later,
.ask__ghost {
  height: 34px;
  padding: 0 12px;
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}
.ask__later:hover,
.ask__ghost:hover {
  background: var(--fill);
}
.ask__hint {
  margin-left: 2px;
  color: var(--faint);
  font-size: 12px;
}

/* ── 回执 ─────────────────────────────────────────────── */
.ask__receipt-main {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin: 0 0 3px;
  color: var(--ink);
  font-size: 13px;
}
.ask__ok {
  color: var(--ok);
  font-weight: 700;
}
.ask__receipt-note,
.ask__receipt-follow {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}
/* 更正语义：上一版留着，不假装没答过。 */
.ask__receipt-was {
  margin: 0 0 3px;
  color: var(--faint);
  font-size: 12px;
  line-height: 1.55;
}
.ask__receipt-follow {
  margin-bottom: 9px;
}
.ask__ghost {
  height: 28px;
  padding: 0 10px;
  font-size: 12px;
}

.ask__deferred-text {
  margin: 0 0 9px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

/* ── 已答清单 ──────────────────────────────────────────── */
.flow__echo {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}
.flow__echo-title {
  margin: 0 0 8px;
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
}
.flow__echo-count {
  color: var(--faint);
  font-weight: 400;
}
.flow__echo-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.flow__echo-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}
.flow__echo-q {
  flex: 0 0 auto;
  min-width: 76px;
  color: var(--faint);
}
.flow__echo-a {
  color: var(--ink);
}
.flow__echo-a--soft {
  color: var(--faint);
}

.flow__persist {
  margin: 12px 0 0;
  color: var(--faint);
  font-size: 11px;
  line-height: 1.6;
}

/* ── 移动端 ────────────────────────────────────────────── */
/* 窄屏上动作行换行，主操作先占一整行：拇指第一下要落在「提交回答」上。 */
@media (max-width: 480px) {
  .flow {
    max-width: none;
    padding: 12px;
    border-radius: var(--radius-md);
  }
  .flow__head {
    flex-wrap: wrap;
  }
  .flow__progress {
    flex: 1 1 100%;
    justify-content: space-between;
  }
  .ask {
    padding: 12px 12px 10px;
    border-radius: var(--radius-md);
  }
  .ask__actions {
    flex-wrap: wrap;
  }
  .ask__primary,
  .ask__later {
    flex: 1 1 100%;
    height: 40px;
    font-size: 14px;
  }
  .ask__hint {
    flex: 1 1 100%;
    margin-left: 0;
    text-align: center;
  }
  .ask__confirm-actions {
    flex-wrap: wrap;
  }
  .ask__confirm-actions .ask__primary,
  .ask__confirm-actions .ask__ghost {
    flex: 1 1 100%;
    height: 40px;
  }
  .flow__echo-q {
    min-width: 62px;
  }
}
</style>
