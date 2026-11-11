<script setup lang="ts">
// The course 空间's pages, beside its sidebar. 题目 (all, or one 分类): each
// 题目 with its 分类, how it is graded, deadline, whether it is claimed alone
// (个人) or by a team (组队 and the team size), where I or my team stand, and
// 领取 or 进入. 领取 makes a project owned by a team: a personal 题目 goes to my
// own team; one a team may claim asks 「自己领」 or 「和团队一起领」 and which
// team, and every member of that team is in the one project it gets.
// Opening a 题目 shows what it asks for (提交项), how it is graded, and its
// 提交记录: submissions are made to the 题目 and their results live here, not
// in the project. Each one names its time, who submitted (as which team), the
// version it pinned and its result: the judge's verdict, or 待老师评 until the
// teacher's score and comment arrive. Opening one shows that version read-only.
// 公告 is the teacher's notices; 资料库 the slides and handouts. A space has no
// channels and holds no one's project.
import type { Submission } from './course'

import { computed, ref, watch } from 'vue'

import {
  ANNOUNCEMENTS,
  dueChip,
  GRADING_NOTE,
  GRADING_TEXT,
  LIBRARY,
  modeText,
  submitItems,
  topicById,
  TOPICS,
  UNITS,
} from './course'
import { asContext, CLAIMED, ME, NAMES, projectTeam, SPACE, teamOf, TEAMS } from './data'
import JudgeResult from './JudgeResult.vue'
import { whenText } from './shapes'
import {
  claim,
  claimAs,
  openSpace,
  openSubmit,
  openVersion,
  resultOf,
  S,
  showTopic,
  standing,
  switchProject,
  toast,
} from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import { monthDayTime } from '@/lib/dateText'

const ICONS: Record<string, string> = {
  Lab: 'mdi-flask-outline',
  题单: 'mdi-format-list-numbered',
  大作业: 'mdi-account-group-outline',
}
const ROLE: Record<string, string> = { zhanglaoshi: '老师', lizhujiao: '助教' }

const rows = computed(() =>
  TOPICS.filter((x) => !S.spaceCat || x.category === S.spaceCat).map((x) => ({
    ...x,
    st: standing(x.id),
    chip: dueChip(x.due),
    claimed: !!x.project && S.claimed.includes(x.project),
  }))
)

// ---- one 题目 open: what it asks for, and its 提交记录 ----
const detail = computed(() => (S.spacePage === 'tasks' && S.spaceTopic ? topicById(S.spaceTopic) ?? null : null))
const claimedHere = computed(() => !!detail.value?.project && S.claimed.includes(detail.value.project))
const subs = computed(() => (detail.value ? S.subs[detail.value.id] ?? [] : []))
/** 提交项 as one line; a 题单's are per problem. */
const itemsText = computed(() => {
  const t = detail.value
  if (!t) return ''
  if (t.category === '题单') return '每题一份 main.cpp，在题单里一题一题提交'
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

// ---- 领取: 自己领 or 和团队一起领 ----
const asked = computed(() => (S.claimAsk ? topicById(S.claimAsk) ?? null : null))
const groups = TEAMS.filter((x) => !x.personal)
/** A team can claim this 题目 when its size fits: exactly `size` for a team 题目, up to `size` for either. */
function fits(team: (typeof TEAMS)[number]): boolean {
  const t = asked.value
  if (!t?.size) return false
  return t.mode === 'team' ? team.members.length === t.size : team.members.length <= t.size
}
const choice = ref<string>(ME)
watch(asked, (t) => {
  if (t) choice.value = t.mode === 'team' ? groups.find(fits)?.handle ?? '' : ME
})

const size = (b: number) => (b >= 1_000_000 ? `${(b / 1_000_000).toFixed(1)} MB` : `${Math.round(b / 1000)} KB`)
const title = computed(() =>
  S.spacePage === 'announce'
    ? '公告'
    : S.spacePage === 'library'
      ? '资料库'
      : detail.value?.title ?? S.spaceCat ?? '全部题目'
)
</script>

<template>
  <section class="ot-space" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">{{ title }}</h2>
      <span class="ot-head__sub">{{ SPACE.name }} · {{ SPACE.org }} · {{ SPACE.teacher }}</span>
    </header>

    <div v-if="detail" class="ot-space__body" data-zone="topic">
      <button type="button" class="ot-tp__back" @click="openSpace('tasks')">
        <v-icon size="16" icon="mdi-chevron-left" />全部题目
      </button>
      <div class="ot-tp__head">
        <v-icon size="20" class="ot-space__kind" :icon="ICONS[detail.category]" />
        <h3 class="ot-tp__title">{{ detail.title }}</h3>
        <span class="ot-space__type">{{ detail.category }}</span>
        <span class="ot-head__gap" />
        <template v-if="claimedHere">
          <BaseButton kind="secondary" size="sm" @click="switchProject(detail.project!)">进入项目</BaseButton>
          <BaseButton
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
            <span v-if="dueChip(detail.due)" class="ot-space__chip">{{ dueChip(detail.due) }}</span>
          </dd>
        </div>
        <div>
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
        <div v-if="claimedHere">
          <dt>项目</dt>
          <dd>{{ CLAIMED[detail.project!].name }} · 在团队「{{ projectTeam(detail.project!).name }}」下</dd>
        </div>
      </dl>

      <section class="ot-tp__subs" data-zone="topic-subs">
        <h4 class="ot-tp__h">
          提交记录<span>{{ subs.length || '' }}</span>
        </h4>
        <p v-if="!subs.length" class="ot-space__note">
          {{ claimedHere ? '还没有提交。每次提交都记在这里：交的是哪个版本、结果是什么。' : '领取之后才能提交。' }}
        </p>
        <div v-for="x in subs" :key="x.id" class="ot-tp__sub" :class="{ 'is-open': shown === x.id }">
          <button type="button" class="ot-tp__row" :data-sub="x.id" @click="toggle(x.id)">
            <span class="ot-tp__n"
              >{{ detail.category === '题单' ? `${UNITS[x.unit]!.title} · ` : '' }}第 {{ x.n }} 次</span
            >
            <span class="ot-tp__when">{{ whenText(x.at) }}</span>
            <span class="ot-tp__by">
              <UserAvatar :size="18" :name="NAMES[x.who]" :seed="x.who" />{{ NAMES[x.who]
              }}{{ x.team ? ` · 以「${teamOf(x.team).name}」` : '' }}
            </span>
            <span class="ot-tp__ver"
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
                <span class="ot-head__gap" />
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
                <span>版本 {{ x.version }} · 只读</span>
                <span class="ot-head__gap" />
                <BaseButton kind="ghost" size="sm" prepend-icon="mdi-open-in-app" @click="openVersion(x.id)"
                  >在项目里打开</BaseButton
                >
              </div>
              <div class="ot-tp__code"><ChangesDiff :lines="pinned(x)" inline /></div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <div v-else-if="S.spacePage === 'tasks'" class="ot-space__body">
      <p class="ot-space__note">
        领取后就是一个项目：个人题目建在你自己的团队下，组队题目建在领取它的团队下，组里每个人都在里面。一个团队一道题只有一个项目。
      </p>
      <div class="ot-space__list" data-zone="topics">
        <div v-for="t in rows" :key="t.id" class="ot-space__row" :data-topic="t.id">
          <v-icon size="18" class="ot-space__kind" :icon="ICONS[t.category]" />
          <button
            type="button"
            class="ot-space__main"
            :title="`打开「${t.title}」：要求和提交记录`"
            @click="showTopic(t.id)"
          >
            <span class="ot-space__title"
              >{{ t.title }}<span class="ot-space__type">{{ t.category }}</span
              ><span class="ot-space__type" :data-grading="t.grading">{{ GRADING_TEXT[t.grading] }}</span></span
            >
            <span class="ot-space__about">{{ t.about }}</span>
          </button>
          <span class="ot-space__mode" :class="{ 'is-team': t.mode !== 'solo' }">
            <v-icon size="14" :icon="t.mode === 'solo' ? 'mdi-account-outline' : 'mdi-account-multiple-outline'" />{{
              modeText(t)
            }}
          </span>
          <div class="ot-space__due">
            <span>截止 {{ monthDayTime(Date.parse(t.due)) }}</span>
            <span v-if="t.chip" class="ot-space__chip">{{ t.chip }}</span>
          </div>
          <span class="ot-space__st" :class="`is-${t.st.tone}`" :title="t.st.text">{{ t.st.text }}</span>
          <BaseButton
            v-if="t.claimed"
            kind="secondary"
            size="sm"
            class="ot-space__btn"
            @click="switchProject(t.project!)"
            >进入</BaseButton
          >
          <BaseButton
            v-else
            :id="`b-claim-${t.id}`"
            kind="secondary"
            size="sm"
            class="ot-space__btn"
            @click="claim(t.id)"
            >领取</BaseButton
          >
        </div>
      </div>
    </div>

    <div v-else-if="S.spacePage === 'announce'" class="ot-space__body" data-zone="announcements">
      <article v-for="a in ANNOUNCEMENTS" :key="a.id" class="ot-space__ann">
        <div class="ot-space__annhead">
          <UserAvatar :size="24" :name="NAMES[a.who]" :seed="a.who" />
          <span class="ot-space__who">{{ NAMES[a.who] }}</span>
          <span class="ot-space__role">{{ ROLE[a.who] }}</span>
          <span v-if="a.pinned" class="ot-space__pin"><v-icon size="12" icon="mdi-pin-outline" />置顶</span>
          <span class="ot-head__gap" />
          <span class="ot-space__at">{{ whenText(a.at) }}</span>
        </div>
        <h3 class="ot-space__anntitle">{{ a.title }}</h3>
        <p class="ot-space__annbody">{{ a.body }}</p>
      </article>
    </div>

    <div v-else class="ot-space__body" data-zone="library">
      <div class="ot-space__list">
        <button
          v-for="f in LIBRARY"
          :key="f.name"
          type="button"
          class="ot-space__file"
          @click="toast(`演示里不打开文件：${f.name}`)"
        >
          <v-icon
            size="18"
            class="ot-space__kind"
            :icon="f.kind === 'slides' ? 'mdi-presentation' : 'mdi-file-document-outline'"
          />
          <span class="ot-space__fname">{{ f.name }}</span>
          <span class="ot-space__ftype">{{ f.kind === 'slides' ? '课件' : '讲义' }}</span>
          <span class="ot-space__fsize">{{ size(f.bytes) }}</span>
          <span class="ot-space__fby">{{ NAMES[f.who] }}</span>
          <span class="ot-space__at">{{ whenText(f.at) }}</span>
        </button>
      </div>
    </div>

    <div v-if="asked" class="ot-claim" data-zone="claim">
      <div class="ot-claim__card" role="dialog" aria-modal="true" :aria-label="`领取「${asked.title}」`">
        <h3 class="t-title ot-claim__title">领取「{{ asked.title }}」</h3>
        <p class="ot-claim__lead">
          领取后建一个项目，题面、起始代码和评测都在里面。这道题{{
            asked.mode === 'team' ? `要 ${asked.size} 人组队` : `可以自己做，也可以 ${asked.size} 人以内组队`
          }}。
        </p>
        <button
          type="button"
          class="ot-claim__opt"
          :class="{ 'is-on': choice === ME }"
          :disabled="asked.mode === 'team'"
          data-claim="self"
          @click="choice = ME"
        >
          <v-icon size="18" :icon="choice === ME ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'" />
          <span class="ot-claim__main">
            <b>自己领</b>
            <span>{{
              asked.mode === 'team' ? `这道题要 ${asked.size} 人组队，不能一个人领` : '项目建在你自己的团队「林晓」下'
            }}</span>
          </span>
        </button>
        <div class="ot-claim__sec">和团队一起领</div>
        <button
          v-for="g in groups"
          :key="g.handle"
          type="button"
          class="ot-claim__opt"
          :class="{ 'is-on': choice === g.handle }"
          :disabled="!fits(g)"
          :data-claim="g.handle"
          @click="choice = g.handle"
        >
          <v-icon size="18" :icon="choice === g.handle ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'" />
          <span class="ot-claim__main">
            <b>{{ g.name }}</b>
            <span>{{
              fits(g)
                ? `${g.members.map((h) => NAMES[h]).join('、')}：${g.members.length} 个人共用这一个项目`
                : `${g.members.length} 人，这道题要 ${asked.size} 人`
            }}</span>
          </span>
          <span class="ot-claim__faces">
            <UserAvatar v-for="h in g.members.slice(0, 5)" :key="h" :size="20" :name="NAMES[h]" :seed="h" />
          </span>
        </button>
        <div class="ot-claim__foot">
          <BaseButton kind="ghost" size="sm" @click="S.claimAsk = null">取消</BaseButton>
          <BaseButton
            id="b-claim-confirm"
            kind="primary"
            size="sm"
            :disabled="!choice"
            @click="claimAs(asked.id, choice)"
            >{{ choice === ME ? '自己领' : `以「${TEAMS.find((x) => x.handle === choice)?.name}」领取` }}</BaseButton
          >
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-space {
  display: flex;
  position: relative;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-space__body {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 32px;
  overflow: auto;
}

.ot-space__note {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-space__list {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-space__row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 60px;
  padding: 8px 12px 8px 16px;
}

.ot-space__row + .ot-space__row,
.ot-space__file + .ot-space__file {
  border-top: 1px solid var(--line);
}

.ot-space__kind {
  flex: none;
  color: var(--muted);
}

.ot-space__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 4px 6px;
  margin: -4px -6px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-space__main:hover {
  background: var(--fill);
}

.ot-space__title {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.ot-space__type {
  margin-left: 8px;
  padding: 0 6px;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-space__about {
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-space__mode {
  display: inline-flex;
  flex: none;
  gap: 4px;
  align-items: center;
  width: 112px;
  font-size: 12px;
  color: var(--muted);
}

.ot-space__mode.is-team {
  color: var(--ink);
}

.ot-space__due {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  align-items: flex-end;
  width: 112px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

/* Due soon: the same amber as anything else that waits on me. */
.ot-space__chip {
  padding: 0 6px;
  line-height: 18px;
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}

.ot-space__st {
  flex: none;
  width: 150px;
  overflow: hidden;
  font-size: 13px;
  color: var(--muted);
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-space__st.is-busy {
  color: var(--ink);
}

.ot-space__st.is-done {
  color: var(--ok-ink);
}

.ot-space__btn {
  flex: none;
  width: 64px;
}

/* One 题目 open */
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

.ot-tp__head .ot-space__type {
  margin-left: 0;
}

.ot-tp__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
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

.ot-tp__subs {
  margin-top: 20px;
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

/* 公告 */
.ot-space__ann {
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-space__ann + .ot-space__ann {
  margin-top: 12px;
}

.ot-space__annhead {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ot-space__who {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.ot-space__role,
.ot-space__at {
  font-size: 12px;
  color: var(--muted);
}

.ot-space__pin {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 0 6px;
  font-size: 12px;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-space__anntitle {
  margin: 10px 0 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.ot-space__annbody {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

/* 资料库 */
.ot-space__file {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
  height: 48px;
  padding: 0 16px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-space__file:hover {
  background: var(--fill);
}

.ot-space__fname {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-space__file .ot-space__at {
  flex: none;
  width: 88px;
  text-align: right;
}

.ot-space__ftype,
.ot-space__fsize,
.ot-space__fby {
  flex: none;
  width: 64px;
  font-size: 12px;
  color: var(--muted);
}

/* 领取: 自己领 or 和团队一起领, over the list. */
.ot-claim {
  display: grid;
  position: absolute;
  inset: 0;
  z-index: 2;
  place-items: center;
  background: var(--scrim, rgb(0 0 0 / 28%));
}

.ot-claim__card {
  width: 460px;
  padding: 20px;
  background: var(--surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.ot-claim__title {
  margin: 0 0 6px;
}

.ot-claim__lead {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-claim__sec {
  margin: 12px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--faint);
}

.ot-claim__opt {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 10px 12px;
  font: inherit;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-claim__opt + .ot-claim__opt {
  margin-top: 6px;
}

.ot-claim__opt.is-on {
  border-color: var(--ink);
}

.ot-claim__opt:disabled {
  cursor: default;
  opacity: 0.5;
}

.ot-claim__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  font-size: 12px;
  color: var(--muted);
}

.ot-claim__main b {
  font-size: 14px;
  color: var(--ink);
}

.ot-claim__faces {
  display: flex;
  gap: 2px;
}

.ot-claim__foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
