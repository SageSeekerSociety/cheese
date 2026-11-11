<script setup lang="ts">
// 提交入口 in a project claimed from a 题目: one quiet line under the
// project's header, 「● ICS 2026 · 11月14日截止 ›」. The dot says where my (or
// my team's) submission stands: grey 没交, green 已交, blue 有新批改 (the line
// then says 「有新批改」 instead of the date). The line names the course, not
// the 题目: the header right above it already names the project, which is the
// 题目's name, and a long one (「Shell Lab 大作业」) would not fit beside the
// date. A click opens a calm card in place and folds it again. The card leads
// with what I have done: the latest 运行 (「运行：8/13 个函数通过」,
// 「trace 通过 3/8」, a 题单's 「已做 2/5」, or 「还没运行」; 「本地…」 under
// 方案 T3, where runs are in my own terminal; under T2 nobody in a course
// project runs code, so it stays 「还没运行」), then the
// latest submission, then the actions: 「看题目」 back to the 题目 in the
// space and 「提交」. The date stays on the line only; inside, an amber
// 「还剩 20 小时」 shows in the last 24 hours. Nothing on it is orange, and 提交
// is a secondary button. It opens by itself only in the last 24 hours or
// when a result I have not looked at has come in; folding it, or opening the
// 题目, counts as having looked, and a blue 「新」 marks that result on the card.
import { computed, watch } from 'vue'

import { CLOCK, hoursLeft, SET, topicOf } from './course'
import { isClaim, SPACE } from './data'
import { latestOf, markSeen, openSubmit, passed, resultOf, S, showTopic, unseenResult } from './state'
import { V } from './variants'

import BaseButton from '@/components/base/BaseButton.vue'
import { monthDay } from '@/lib/dateText'

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

/** The card's first line: the latest 运行 (in the project's environment, or in my own terminal under T3). */
const progress = computed(() => {
  if (!t.value) return ''
  const where = V.run === 'T3' ? '本地' : ''
  if (isSet.value) {
    const did = SET.filter((x) => {
      const r = S.ran[x.id]
      return passed(latestOf(t.value!.id, x.id)) || (!!r && r.ok === r.total)
    }).length
    const touched = SET.some((x) => S.ran[x.id]) || subs.value.length > 0
    return touched ? `已做 ${did}/${SET.length}` : `${where}还没运行`
  }
  const r = S.ran[t.value.id]
  if (!r) return `${where}还没运行`
  return t.value.id === 'shelllab'
    ? `${where}运行：trace 通过 ${r.ok}/${r.total}`
    : `${where}运行：${r.ok}/${r.total} 个函数通过`
})
/** The card's second line: the latest submission; a result I have not looked at says only what came back. */
const submitted = computed(() => {
  if (!t.value) return ''
  if (fresh.value) {
    const r = resultOf(fresh.value)
    return fresh.value.review ? `张老师批改了：${r.text}` : `第 ${fresh.value.n} 次评测：${r.text}`
  }
  if (!last.value) return '还没提交'
  if (isSet.value) return `提交通过 ${setPassed.value}/${SET.length}`
  const r = resultOf(last.value)
  if (r.tone === 'wait') return t.value.grading === 'teacher' ? '已提交 · 等张老师批改' : '最近提交 评测中…'
  return `最近提交 ${r.text}`
})

// ---- folded by default, opened by hand or by itself ----
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
    <button
      type="button"
      class="ot-due"
      :class="{ 'is-open': open }"
      data-origin-line
      :aria-expanded="open"
      :title="open ? '收起' : `来自 ${SPACE.name} · ${t.title}：展开看交到哪了、提交`"
      @click="toggle"
    >
      <span class="ot-due__dot" :class="`is-${dot}`" :title="dotTitle[dot]" />
      <span class="ot-due__text">{{ SPACE.brief }}</span>
      <span v-if="fresh" class="ot-due__tail ot-due__new">· 有新批改</span>
      <span v-else class="ot-due__tail" :class="{ 'ot-due__soon': soon }">· {{ monthDay(Date.parse(t.due)) }}截止</span>
      <v-icon size="14" icon="mdi-chevron-right" class="ot-due__chev" />
    </button>

    <section v-if="open" class="ot-card" data-origin aria-label="来自的题目">
      <div class="ot-card__lead" data-origin-progress>{{ progress }}</div>
      <div class="ot-card__line" data-origin-status><b v-if="fresh" class="ot-card__new">新</b>{{ submitted }}</div>
      <div v-if="soon" class="ot-card__soon" data-origin-soon>{{ countdown }}</div>
      <div class="ot-card__foot">
        <button
          type="button"
          class="ot-card__link"
          data-origin-topic
          :title="`在空间里打开「${t.title}」`"
          @click="look"
        >
          {{ fresh ? '看批改' : '看题目' }}<v-icon size="13" icon="mdi-chevron-right" />
        </button>
        <BaseButton id="b-submit" kind="secondary" size="sm" @click="openSubmit(t.id)">提交</BaseButton>
      </div>
    </section>
  </template>
</template>

<style scoped>
/* The line: one muted row that reads as part of the header, a status dot in front. */
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

/* The card, opened under the line: all neutral. */
.ot-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 2px 8px 0;
  padding: 8px 10px 10px;
  background: var(--fill);
  border-radius: var(--radius-md);
}

/* What I have done leads: body text, a step above the rest. */
.ot-card__lead {
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

/* The last day only, and amber like anything else that waits on me, never the orange of 提交. */
.ot-card__soon {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--warn-ink);
}

.ot-card__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

.ot-card__link {
  display: inline-flex;
  flex: none;
  align-items: center;
  padding: 0;
  font: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--text);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-card__link:hover {
  color: var(--ink);
  text-decoration: underline;
}
</style>
