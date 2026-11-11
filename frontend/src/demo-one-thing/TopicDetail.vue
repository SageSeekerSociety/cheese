<script setup lang="ts">
// One 题目: what it asks for (提交项), how it is graded, its deadline, and its
// 提交记录. Submissions are made to the 题目 and their results live here, not
// in the project. Each one names its time, who submitted (as which team), the
// version it pinned and its result: the judge's verdict, or 待老师评 until the
// teacher's score and comment arrive. Opening one shows that version read-only.
// The space shows it in its center column; under 方案 C the claimed project's
// right panel embeds the same view (`embedded`: narrower, with 「在空间里打开」).
// A 题单 (in every 方案 but D, where its rows stay in the 题单 document) lists
// its problems here, one row each with where it stands and its own 「提交」.
import type { Submission } from './course'

import { computed, ref, watch } from 'vue'

import { dueChip, GRADING_NOTE, GRADING_TEXT, modeText, SET, submitItems, topicById, UNITS } from './course'
import { asContext, CLAIMED, NAMES, projectTeam, teamOf } from './data'
import JudgeResult from './JudgeResult.vue'
import { whenText } from './shapes'
import {
  claim,
  latestOf,
  openFile,
  openSpace,
  openSubmit,
  openVersion,
  passed,
  resultOf,
  S,
  showTopic,
  switchProject,
} from './state'
import { V } from './variants'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import { monthDayTime } from '@/lib/dateText'

const props = defineProps<{ topicId: string; embedded?: boolean }>()

const ICONS: Record<string, string> = {
  Lab: 'mdi-flask-outline',
  题单: 'mdi-format-list-numbered',
  大作业: 'mdi-account-group-outline',
}

const detail = computed(() => topicById(props.topicId)!)
const claimedHere = computed(() => !!detail.value.project && S.claimed.includes(detail.value.project))
const subs = computed(() => S.subs[detail.value.id] ?? [])
const isSet = computed(() => detail.value.category === '题单')
/** The 题单's problems are listed (and submitted) here, except under 方案 D. */
const setRows = computed(() =>
  isSet.value && V.submit !== 'D'
    ? SET.map((p) => {
        const last = latestOf(detail.value.id, p.id)
        return {
          ...p,
          tries: subs.value.filter((x) => x.unit === p.id).length,
          result: last ? resultOf(last).text : '—',
          ok: passed(last),
          state: passed(last) ? '通过' : last?.verdict ? '没通过' : last ? '评测中' : '未提交',
          bad: !!last?.verdict && !passed(last),
        }
      })
    : []
)
/** 提交项 as one line; a 题单's are per problem. */
const itemsText = computed(() => {
  const t = detail.value
  if (t.category === '题单')
    return V.submit === 'D' ? '每题一份 main.cpp，在题单里一题一题提交' : '每题一份 main.cpp，在下面一题一题提交'
  return submitItems(t, t.id)
    .map((x) => `${x.type === 'FILE' ? '文件' : '文字'} ${x.prompt}`)
    .join(' + ')
})
// The latest submission starts open; a click opens another or folds it.
const openSub = ref<string | null>(null)
watch(detail, () => (openSub.value = null))
const shown = computed(() => openSub.value ?? subs.value[0]?.id ?? '')
const toggle = (id: string) => (openSub.value = shown.value === id ? '' : id)
const pinned = (x: Submission) => asContext(x.path, x.code.replace(/\n$/, '').split('\n'))

/** A 题单 problem's row opens its main.cpp in the project. */
function openProblem(id: string) {
  const p = detail.value.project
  if (!p || !claimedHere.value) return
  if (S.area !== 'project' || S.project !== p) switchProject(p)
  openFile(`${id}/main.cpp`, props.embedded ? { ftab: 'topic' } : {})
}
</script>

<template>
  <div class="ot-tp" :class="{ 'ot-tp--embedded': embedded }" data-zone="topic">
    <button v-if="!embedded" type="button" class="ot-tp__back" @click="openSpace('tasks')">
      <v-icon size="16" icon="mdi-chevron-left" />全部题目
    </button>
    <div class="ot-tp__head">
      <v-icon size="20" class="ot-tp__icon" :icon="ICONS[detail.category]" />
      <h3 class="ot-tp__title">{{ detail.title }}</h3>
      <span class="ot-tp__type">{{ detail.category }}</span>
      <span class="ot-tp__gap" />
      <button
        v-if="embedded"
        type="button"
        class="ot-tp__out"
        data-topic-in-space
        :title="`在空间里打开「${detail.title}」`"
        @click="showTopic(detail.id)"
      >
        在空间里打开<v-icon size="13" icon="mdi-arrow-top-right" />
      </button>
      <template v-else-if="claimedHere">
        <BaseButton kind="secondary" size="sm" @click="switchProject(detail.project!)">进入项目</BaseButton>
        <BaseButton
          v-if="!setRows.length"
          id="b-topic-submit"
          kind="primary"
          size="sm"
          prepend-icon="mdi-upload-outline"
          @click="openSubmit(detail.id)"
          >提交</BaseButton
        >
      </template>
      <BaseButton v-else kind="primary" size="sm" @click="claim(detail.id)">领取</BaseButton>
    </div>
    <p class="ot-tp__about">{{ detail.about }}</p>
    <dl class="ot-tp__facts">
      <div>
        <dt>截止</dt>
        <dd>
          {{ monthDayTime(Date.parse(detail.due)) }}
          <span v-if="dueChip(detail.due)" class="ot-tp__chip">{{ dueChip(detail.due) }}</span>
        </dd>
      </div>
      <div v-if="!embedded">
        <dt>谁来做</dt>
        <dd>{{ modeText(detail) }}</dd>
      </div>
      <div>
        <dt>评分方式</dt>
        <dd data-grading>
          <b>{{ GRADING_TEXT[detail.grading] }}</b> · {{ GRADING_NOTE[detail.grading] }}
        </dd>
      </div>
      <div>
        <dt>提交项</dt>
        <dd>{{ itemsText }}</dd>
      </div>
      <div v-if="claimedHere && !embedded">
        <dt>项目</dt>
        <dd>{{ CLAIMED[detail.project!].name }} · 在团队「{{ projectTeam(detail.project!).name }}」下</dd>
      </div>
    </dl>
    <BaseButton
      v-if="embedded && claimedHere && !setRows.length"
      id="b-panel-submit"
      class="ot-tp__submit"
      kind="primary"
      size="sm"
      prepend-icon="mdi-upload-outline"
      @click="openSubmit(detail.id)"
      >提交</BaseButton
    >

    <section v-if="setRows.length" class="ot-tp__set" data-zone="set-problems">
      <h4 class="ot-tp__h">
        题目<span>通过 {{ setRows.filter((r) => r.ok).length }}/{{ setRows.length }}</span>
      </h4>
      <div class="ot-tp__setcols t-eyebrow-read">
        <span>题</span><span>状态</span><span v-if="!embedded">尝试</span><span v-if="!embedded">最近得分</span>
      </div>
      <div v-for="r in setRows" :key="r.id" class="ot-tp__setrow" :data-problem="r.id">
        <button
          type="button"
          class="ot-tp__setmain"
          :disabled="!claimedHere"
          :title="claimedHere ? `在项目里打开 ${r.id}/main.cpp` : undefined"
          @click="openProblem(r.id)"
        >
          <span class="ot-tp__setname"
            ><code>{{ r.id }}</code> {{ r.title }}</span
          >
          <span class="ot-tp__setst" :class="{ 'is-ok': r.ok, 'is-bad': r.bad }">
            <v-icon v-if="r.ok" size="14" icon="mdi-check" />{{ r.state }}
          </span>
          <span v-if="!embedded" class="ot-tp__setn">{{ r.tries ? `${r.tries} 次` : '—' }}</span>
          <span v-if="!embedded" class="ot-tp__setn">{{ r.result }}</span>
        </button>
        <BaseButton
          v-if="claimedHere"
          kind="secondary"
          size="sm"
          class="ot-tp__setsubmit"
          :data-problem-submit="r.id"
          @click="openSubmit(detail.id, r.id)"
          >提交</BaseButton
        >
      </div>
    </section>

    <section class="ot-tp__subs" data-zone="topic-subs">
      <h4 class="ot-tp__h">
        提交记录<span>{{ subs.length || '' }}</span>
      </h4>
      <p v-if="!subs.length" class="ot-tp__note">
        {{ claimedHere ? '还没有提交。每次提交都记在这里：交的是哪个版本、结果是什么。' : '领取之后才能提交。' }}
      </p>
      <div v-for="x in subs" :key="x.id" class="ot-tp__sub" :class="{ 'is-open': shown === x.id }">
        <button type="button" class="ot-tp__row" :data-sub="x.id" @click="toggle(x.id)">
          <span class="ot-tp__n">{{ isSet ? `${UNITS[x.unit]!.title} · ` : '' }}第 {{ x.n }} 次</span>
          <span class="ot-tp__when">{{ whenText(x.at) }}</span>
          <span class="ot-tp__by">
            <template v-if="!embedded">
              <UserAvatar :size="18" :name="NAMES[x.who]" :seed="x.who" />{{ NAMES[x.who]
              }}{{ x.team ? ` · 以「${teamOf(x.team).name}」` : '' }}
            </template>
          </span>
          <span v-if="!embedded" class="ot-tp__ver"
            >版本 <code>{{ x.version }}</code></span
          >
          <span class="ot-tp__res" :class="`is-${resultOf(x).tone}`">{{ resultOf(x).text }}</span>
          <v-icon size="16" class="ot-tp__chev" :icon="shown === x.id ? 'mdi-chevron-up' : 'mdi-chevron-down'" />
        </button>
        <div v-if="shown === x.id" class="ot-tp__detail">
          <JudgeResult v-if="x.verdict" :verdict="x.verdict" />
          <div v-else-if="x.review" class="ot-tp__review" data-review>
            <div class="ot-tp__rhead">
              <UserAvatar :size="22" :name="NAMES[x.review.by]" :seed="x.review.by" />
              <b>{{ NAMES[x.review.by] }}</b>
              <span>批改 · {{ whenText(x.review.at) }}</span>
              <span class="ot-tp__gap" />
              <b class="ot-tp__score">{{ x.review.score }}/{{ x.review.total }}</b>
            </div>
            <p>{{ x.review.comment }}</p>
          </div>
          <div v-else class="ot-tp__wait">
            <v-icon size="15" icon="mdi-timer-sand" />{{
              detail.grading === 'teacher' ? '待老师评：张老师批改后，分数和评语出现在这里' : '评测中…'
            }}
          </div>
          <div v-for="tx in x.texts" :key="tx.prompt" class="ot-tp__part">
            <div class="ot-tp__parthead">
              <span class="ot-tp__kind">文字</span><b>{{ tx.prompt }}</b>
            </div>
            <p class="ot-tp__text">{{ tx.text }}</p>
          </div>
          <div class="ot-tp__part">
            <div class="ot-tp__parthead">
              <span class="ot-tp__kind">文件</span><code>{{ x.path }}</code>
              <span>版本 {{ x.version }}{{ embedded ? '' : ' · 只读' }}</span>
              <span class="ot-tp__gap" />
              <BaseButton kind="ghost" size="sm" prepend-icon="mdi-open-in-app" @click="openVersion(x.id)">{{
                embedded ? '打开' : '在项目里打开'
              }}</BaseButton>
            </div>
            <div class="ot-tp__code"><ChangesDiff :lines="pinned(x)" inline /></div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ot-tp__back {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 2px 6px 2px 2px;
  margin: 0 0 8px -2px;
  font: inherit;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-tp__back:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-tp__head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ot-tp__icon {
  flex: none;
  color: var(--muted);
}

.ot-tp__gap {
  flex: 1;
}

.ot-tp__type {
  flex: none;
  padding: 0 6px;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-tp__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-tp__out {
  display: inline-flex;
  flex: none;
  gap: 2px;
  align-items: center;
  padding: 2px 4px;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-tp__out:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-tp__about {
  margin: 6px 0 12px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.ot-tp__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 24px;
  padding: 12px 16px;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-tp__facts > div {
  display: flex;
  gap: 12px;
  align-items: baseline;
  min-width: 0;
}

.ot-tp__facts dt {
  flex: none;
  width: 56px;
  font-size: 12px;
  color: var(--muted);
}

.ot-tp__facts dd {
  min-width: 0;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-tp__facts dd b {
  font-weight: 600;
  color: var(--ink);
}

/* Due soon: the same amber as anything else that waits on me. */
.ot-tp__chip {
  padding: 0 6px;
  font-size: 12px;
  line-height: 18px;
  color: var(--accent-ink);
  white-space: nowrap;
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}

.ot-tp__note {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-tp__h {
  display: flex;
  gap: 6px;
  align-items: baseline;
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.ot-tp__h span {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

/* A 题单's problems: one row each, the row opens its main.cpp, 「提交」 beside it. */
.ot-tp__set {
  margin-top: 20px;
}

.ot-tp__setcols {
  display: flex;
  gap: 10px;
  /* Clear of each row's 提交 (56px), the gaps and paddings around it: 8 + 8 + 56 + 8. */
  padding: 0 80px 2px 8px;
}

.ot-tp__setrow {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-right: 8px;
  border-top: 1px solid var(--line);
}

.ot-tp__setmain {
  display: flex;
  flex: 1;
  gap: 10px;
  align-items: center;
  min-width: 0;
  height: 40px;
  padding: 0 8px;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-tp__setmain:hover:not(:disabled) {
  background: var(--fill);
}

.ot-tp__setmain:disabled {
  cursor: default;
}

.ot-tp__setsubmit {
  flex: none;
  width: 56px;
}

.ot-tp__setcols > :first-child,
.ot-tp__setname {
  flex: 1;
  min-width: 0;
}

.ot-tp__setcols > :not(:first-child),
.ot-tp__setst,
.ot-tp__setn {
  flex: none;
  width: 64px;
  text-align: right;
}

.ot-tp__setname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-tp__setname code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}

.ot-tp__setst {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  justify-content: flex-end;
  font-size: 13px;
  color: var(--muted);
}

.ot-tp__setst.is-ok {
  color: var(--ok-ink);
}

.ot-tp__setst.is-bad {
  color: var(--text);
}

.ot-tp__setn {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}

.ot-tp__subs {
  margin-top: 20px;
}

.ot-tp__sub {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-tp__sub + .ot-tp__sub {
  margin-top: 8px;
}

.ot-tp__row {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
  height: 44px;
  padding: 0 12px 0 16px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-lg);
}

.ot-tp__row:hover {
  background: var(--fill);
}

.ot-tp__n {
  flex: none;
  font-weight: 600;
  color: var(--ink);
}

.ot-tp__when {
  flex: none;
  width: 88px;
  color: var(--muted);
}

.ot-tp__by {
  display: inline-flex;
  flex: 1;
  gap: 6px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.ot-tp__ver {
  flex: none;
  color: var(--muted);
}

.ot-tp__ver code,
.ot-tp__part code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
}

.ot-tp__res {
  flex: none;
  min-width: 64px;
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--ink);
  text-align: right;
}

.ot-tp__res.is-ok {
  color: var(--ok-ink);
}

.ot-tp__res.is-wait {
  font-family: var(--font-sans);
  font-weight: 400;
  color: var(--accent-ink);
}

.ot-tp__chev {
  flex: none;
  color: var(--muted);
}

.ot-tp__detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 16px 16px;
}

.ot-tp__wait {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  color: var(--accent-ink);
}

.ot-tp__review {
  padding: 10px 12px;
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ot-tp__rhead {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
}

.ot-tp__rhead b {
  font-size: 13px;
  color: var(--ink);
}

.ot-tp__rhead .ot-tp__score {
  font-family: var(--font-mono);
  font-size: 15px;
}

.ot-tp__review p,
.ot-tp__text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: pre-wrap;
}

.ot-tp__parthead {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.ot-tp__parthead b {
  font-size: 13px;
  color: var(--ink);
}

.ot-tp__kind {
  padding: 0 6px;
  line-height: 18px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-tp__code {
  max-height: 240px;
  margin-top: 6px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

/* The pinned version drawn as the code view draws it read-only: plain numbered lines. */
.ot-tp__code :deep(.diff-line--hunk),
.ot-tp__code :deep(.diff-line > .diff-line__num:first-of-type) {
  display: none;
}

.ot-tp__code :deep(.diff-line__text) {
  white-space: pre;
}

/* In a project's right panel: one column, tighter. */
.ot-tp--embedded .ot-tp__title {
  font-size: 16px;
}

.ot-tp--embedded .ot-tp__about {
  margin: 4px 0 10px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.ot-tp--embedded .ot-tp__facts {
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
  padding: 10px 12px;
}

.ot-tp--embedded .ot-tp__submit {
  width: 100%;
  margin-top: 12px;
}

.ot-tp--embedded .ot-tp__set,
.ot-tp--embedded .ot-tp__subs {
  margin-top: 16px;
}

.ot-tp--embedded .ot-tp__row {
  gap: 8px;
  padding: 0 8px 0 12px;
}

.ot-tp--embedded .ot-tp__when {
  width: auto;
}

.ot-tp--embedded .ot-tp__detail {
  padding: 4px 12px 12px;
}
</style>
