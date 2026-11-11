<script setup lang="ts">
// 方案 D1, D2 and D3 of 提交入口: calmer versions of D's card at the top of a
// project claimed from a 题目. Nothing on them is orange, and 提交 is a
// secondary button. D1 is D's card in grey: where I stand in plain text
// (「还没交 · 11月14日 23:59 截止」, then 「最近一次 28/33 · 截止前还能再交」),
// with an amber 「还剩 20 小时」 only in the last 24 hours. D2 leads with what
// I have done: the latest local 运行 (「本地运行：9/13 个函数通过」, a 题单's
// 「已做 2/5」), then the latest submission, the deadline in small grey text.
// D3 is one line under the header, a status dot (grey 没交, green 已交, blue
// 有新批改) and the date, which opens D1's card in place and folds it again;
// it opens by itself only in the last 24 hours or when a result I have not
// looked at has come in (the line then says 「有新批改」). Folding it, or
// opening the 题目, counts as having looked. A blue 「新」 marks that result
// on D1's card too.
import { computed, watch } from 'vue'

import { GRADING_TEXT, hoursLeft, SET, topicOf } from './course'
import { isClaim, SPACE } from './data'
import { latestOf, markSeen, openSubmit, passed, resultOf, S, showTopic, unseenResult } from './state'
import { CLOCK, V } from './variants'

import BaseButton from '@/components/base/BaseButton.vue'
import { monthDay, monthDayTime } from '@/lib/dateText'

const t = computed(() => (isClaim(S.project) ? topicOf(S.project) : null))
const subs = computed(() => (t.value ? S.subs[t.value.id] ?? [] : []))
const last = computed(() => subs.value[0] ?? null)
const isSet = computed(() => t.value?.category === '题单')
const setPassed = computed(() => (t.value ? SET.filter((x) => passed(latestOf(t.value!.id, x.id))).length : 0))
const fresh = computed(() => (t.value ? unseenResult(t.value.id) : null))

/** Nothing left to do before the deadline: every problem through, full marks, or handed to the teacher. */
const done = computed(() => {
  if (!t.value || !last.value) return false
  if (isSet.value) return setPassed.value === SET.length
  return t.value.grading === 'teacher' || passed(last.value)
})
const hours = computed(() => (t.value ? hoursLeft(t.value.due) : 999))
/** The only time the card shows a countdown: under a day left and something still to do. */
const soon = computed(() => hours.value >= 0 && hours.value < 24 && !done.value)
const countdown = computed(() => (hours.value < 1 ? '不到 1 小时就截止' : `还剩 ${hours.value} 小时`))

const dueFull = computed(() => (t.value ? monthDayTime(Date.parse(t.value.due)) : ''))

/** D1 / D3: where I stand, one calm line; a result I have not looked at says only what came back. */
const calm = computed(() => {
  if (fresh.value) {
    const r = resultOf(fresh.value)
    return fresh.value.review ? `张老师批改了：${r.text}` : `第 ${fresh.value.n} 次评测：${r.text}`
  }
  if (!t.value || !last.value) return `还没交 · ${dueFull.value} 截止`
  if (isSet.value) return `通过 ${setPassed.value}/${SET.length} · ${done.value ? '全部通过' : '截止前还能再交'}`
  const r = resultOf(last.value)
  if (r.tone === 'wait')
    return t.value.grading === 'teacher' ? `已交第 ${last.value.n} 次 · 等张老师批改` : '最近一次 评测中…'
  return `最近一次 ${r.text} · ${done.value && t.value.grading === 'judge' ? '全部通过' : '截止前还能再交'}`
})

/** D2: the latest local 运行, the first thing the card says. */
const progress = computed(() => {
  if (!t.value) return ''
  if (isSet.value) {
    const did = SET.filter((x) => {
      const r = S.ran[x.id]
      return passed(latestOf(t.value!.id, x.id)) || (!!r && r.ok === r.total)
    }).length
    const touched = SET.some((x) => S.ran[x.id]) || subs.value.length > 0
    return touched ? `已做 ${did}/${SET.length}` : '本地还没运行'
  }
  const r = S.ran[t.value.id]
  if (!r) return '本地还没运行'
  return t.value.id === 'shelllab'
    ? `本地运行：trace 通过 ${r.ok}/${r.total}`
    : `本地运行：${r.ok}/${r.total} 个函数通过`
})
/** D2: the latest submission, under the progress. */
const submitted = computed(() => {
  if (!t.value || !last.value) return ''
  if (isSet.value) return `提交通过 ${setPassed.value}/${SET.length}`
  const r = resultOf(last.value)
  return r.tone === 'wait' && t.value.grading === 'teacher' ? '已提交 · 等张老师批改' : `最近提交 ${r.text}`
})

// ---- D3: one line, opened by hand or by itself ----
const open = computed(() => {
  if (!t.value) return false
  return S.cardOpen[t.value.id] ?? (soon.value || !!fresh.value)
})
function toggle() {
  if (!t.value) return
  const was = open.value
  if (was) markSeen(t.value.id)
  S.cardOpen[t.value.id] = !was
}
// A new result, or the clock moving into the last day, opens it again even after it was folded by hand.
function reopen() {
  if (t.value) delete S.cardOpen[t.value.id]
}
watch(
  () => fresh.value?.id,
  (id) => {
    if (id) reopen()
  }
)
watch(() => CLOCK.dueSoon, reopen)
/** The line has room for the 题目's name without its note: 「Shell Lab 大作业」, not 「…（3 人组队）」. */
const short = computed(() => t.value?.title.replace(/（[^）]*）$/, '') ?? '')
const dot = computed(() => (fresh.value ? 'new' : last.value ? 'done' : 'none'))
const dotTitle = { new: '有新批改', done: '已交', none: '还没交' }

function look() {
  if (!t.value) return
  markSeen(t.value.id)
  showTopic(t.value.id)
}
</script>

<template>
  <template v-if="t">
    <!-- D3: the folded line; the card below is D1's, without its title row (the line has it). -->
    <button
      v-if="V.submit === 'D3'"
      type="button"
      class="ot-due"
      :class="{ 'is-open': open }"
      data-origin-line
      :aria-expanded="open"
      :title="open ? '收起' : '展开：截止、交到哪了、提交'"
      @click="toggle"
    >
      <span class="ot-due__dot" :class="`is-${dot}`" :title="dotTitle[dot]" />
      <span class="ot-due__text">{{ short }}</span>
      <span v-if="fresh" class="ot-due__tail ot-due__new">· 有新批改</span>
      <span v-else class="ot-due__tail" :class="{ 'ot-due__soon': soon }">· {{ monthDay(Date.parse(t.due)) }}截止</span>
      <v-icon size="14" icon="mdi-chevron-right" class="ot-due__chev" />
    </button>

    <section
      v-if="V.submit !== 'D3' || open"
      class="ot-card"
      :class="{ 'ot-card--folded': V.submit === 'D3' }"
      data-origin
      :data-origin-variant="V.submit"
      aria-label="来自的题目"
    >
      <template v-if="V.submit === 'D3'">
        <div class="ot-card__meta">
          <span>{{ SPACE.brief }} · {{ GRADING_TEXT[t.grading] }}</span>
          <button type="button" class="ot-card__link" data-origin-topic @click="look">
            {{ fresh ? '看批改' : '看题目' }}<v-icon size="13" icon="mdi-chevron-right" />
          </button>
        </div>
      </template>
      <template v-else>
        <div class="ot-card__space"><v-icon size="13" icon="mdi-school-outline" />{{ SPACE.name }}</div>
        <div class="ot-card__head">
          <button
            type="button"
            class="ot-card__title"
            :title="`在空间里打开「${t.title}」`"
            data-origin-topic
            @click="look"
          >
            <span>{{ t.title }}</span
            ><v-icon size="14" icon="mdi-chevron-right" />
          </button>
          <span class="ot-card__mode">{{ GRADING_TEXT[t.grading] }}</span>
        </div>
      </template>

      <template v-if="V.submit === 'D2'">
        <div class="ot-card__lead" data-origin-progress>{{ progress }}</div>
        <div v-if="submitted" class="ot-card__line" data-origin-status>
          <b v-if="fresh" class="ot-card__new">新</b>{{ submitted }}
        </div>
        <div class="ot-card__foot">
          <span class="ot-card__due">{{ dueFull }} 截止</span>
          <BaseButton id="b-submit" kind="secondary" size="sm" @click="openSubmit(t.id)">提交</BaseButton>
        </div>
      </template>
      <template v-else>
        <div class="ot-card__line" data-origin-status><b v-if="fresh" class="ot-card__new">新</b>{{ calm }}</div>
        <div class="ot-card__foot">
          <span v-if="soon" class="ot-card__soon" data-origin-soon>{{ countdown }}</span>
          <span v-else />
          <BaseButton id="b-submit" kind="secondary" size="sm" @click="openSubmit(t.id)">提交</BaseButton>
        </div>
      </template>
    </section>
  </template>
</template>

<style scoped>
/* D3's line: A's quiet line with a status dot in front. */
.ot-due {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  padding: 2px 6px;
  margin: 0 10px 2px;
  font: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-due:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-due__dot {
  flex: none;
  width: 6px;
  height: 6px;
  background: var(--faint);
  border-radius: 50%;
}

.ot-due__dot.is-done {
  background: var(--ok);
}

.ot-due__dot.is-new {
  background: var(--signal-blue);
}

.ot-due__text {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-due__tail {
  flex: none;
  margin-left: -2px;
}

.ot-due__new {
  color: var(--signal-blue);
}

.ot-due__soon {
  color: var(--warn-ink);
}

.ot-due__chev {
  flex: none;
  margin-left: -2px;
  transition: transform var(--dur-quick) var(--ease-standard);
}

.ot-due.is-open .ot-due__chev {
  transform: rotate(90deg);
}

/* The card: D's place and shape, all neutral. */
.ot-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 8px 8px 0;
  padding: 8px 10px 10px;
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ot-card--folded {
  margin-top: 2px;
}

.ot-card__space,
.ot-card__meta {
  display: flex;
  gap: 4px;
  align-items: center;
  overflow: hidden;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: nowrap;
}

.ot-card__meta {
  justify-content: space-between;
  margin-bottom: 2px;
}

.ot-card__link {
  display: inline-flex;
  flex: none;
  align-items: center;
  padding: 0;
  font: inherit;
  color: var(--text);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-card__link:hover {
  color: var(--ink);
  text-decoration: underline;
}

.ot-card__head {
  display: flex;
  gap: 6px;
  align-items: center;
}

.ot-card__title {
  display: inline-flex;
  flex: 1;
  align-items: center;
  min-width: 0;
  padding: 0;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-card__title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-card__title .v-icon {
  flex: none;
  color: var(--muted);
}

.ot-card__title:hover span {
  text-decoration: underline;
}

.ot-card__mode {
  flex: none;
  padding: 0 6px;
  font-size: 12px;
  line-height: 18px;
  color: var(--muted);
  background: var(--surface);
  border-radius: var(--radius-sm);
}

/* D2 leads with what I have done: body text, a step above the rest. */
.ot-card__lead {
  margin-top: 2px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-card__line {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-card__new {
  margin-right: 6px;
  font-weight: 600;
  color: var(--signal-blue);
}

.ot-card__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

.ot-card__due {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The last day only, and amber like anything else that waits on me, never the orange of 提交. */
.ot-card__soon {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--warn-ink);
}
</style>
