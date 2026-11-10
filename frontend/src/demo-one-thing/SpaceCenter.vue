<script setup lang="ts">
// 课程主页: the course 空间's own page. It lists the 题目 the teacher published,
// each with its deadline, where I stand and 领取 (「进入」 once claimed): 领取
// creates a project of my own, and from then on the 题目 is that project. Under
// it, 课程讨论: the space's own channels, the latest few messages and a way in.
// Nothing here is any student's project.
import { computed } from 'vue'

import { dueChip, SET, type Topic, TOPICS } from './course'
import { channelOf, COURSE, COURSE_CHANNELS, NAMES } from './data'
import { whenText } from './shapes'
import { claim, go, latestOf, S, switchProject } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { monthDayTime } from '@/lib/dateText'

/** Where a 题目 stands for me: 未领取, 进行中, or 已提交 with the latest score. */
function standing(t: Topic): { text: string; tone: 'new' | 'busy' | 'done' } {
  const p = t.project
  if (!p || !S.claimed.includes(p)) return { text: '未领取', tone: 'new' }
  if (p === 'datalab') {
    const last = latestOf(p, 'datalab')
    return last ? { text: `已提交 · ${last.score}/${last.total}`, tone: 'done' } : { text: '进行中', tone: 'busy' }
  }
  const tried = SET.filter((x) => latestOf(p, x.id))
  const passed = tried.filter((x) => {
    const l = latestOf(p, x.id)!
    return l.score === l.total
  })
  return tried.length
    ? { text: `已提交 · 通过 ${passed.length}/${SET.length}`, tone: 'done' }
    : { text: '进行中', tone: 'busy' }
}

const rows = computed(() => TOPICS.map((t) => ({ ...t, st: standing(t), chip: dueChip(t.due) })))
const claimed = (t: Topic) => !!t.project && S.claimed.includes(t.project)

/** 课程讨论: the newest messages across the space's channels. */
const recent = computed(() =>
  COURSE_CHANNELS.flatMap((c) => (S.chat[c.id] ?? []).map((l) => ({ ...l, chan: c.id })))
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .slice(0, 3)
)
const plain = (text: string) => text.replace(/<@cheese>/g, '@芝士').replace(/[`*]/g, '')
</script>

<template>
  <section class="ot-space" data-zone="center">
    <header class="ot-head">
      <v-icon size="18" icon="mdi-school-outline" class="ot-space__icon" />
      <h2 class="ot-head__title">{{ COURSE.name }}</h2>
      <span class="ot-head__sub">{{ COURSE.team }} · 张老师</span>
    </header>
    <div class="ot-space__body">
      <section class="ot-space__sec" data-zone="topics">
        <div class="ot-space__sechead">
          <h3 class="t-eyebrow-read">题目 · {{ TOPICS.length }}</h3>
          <span class="ot-space__note">每人一个项目：领取后，题面、起始代码和评测都在你自己的项目里</span>
        </div>
        <div class="ot-space__list">
          <div v-for="t in rows" :key="t.id" class="ot-space__row" :data-topic="t.id">
            <v-icon
              size="18"
              class="ot-space__kind"
              :icon="t.kind === 'set' ? 'mdi-format-list-numbered' : 'mdi-flask-outline'"
            />
            <div class="ot-space__main">
              <span class="ot-space__title"
                >{{ t.title }}<span class="ot-space__type">{{ t.kind === 'set' ? '题单' : 'Lab' }}</span></span
              >
              <span class="ot-space__about">{{ t.about }}</span>
            </div>
            <div class="ot-space__due">
              <span>截止 {{ monthDayTime(Date.parse(t.due)) }}</span>
              <span v-if="t.chip" class="ot-space__chip">{{ t.chip }}</span>
            </div>
            <span class="ot-space__st" :class="`is-${t.st.tone}`">{{ t.st.text }}</span>
            <BaseButton
              v-if="claimed(t)"
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
      </section>

      <section class="ot-space__sec" data-zone="course-talk">
        <div class="ot-space__sechead">
          <h3 class="t-eyebrow-read">课程讨论</h3>
          <span class="ot-space__note">全班和老师、助教一起；不属于任何人的项目</span>
          <span class="ot-head__gap" />
          <BaseButton
            v-for="c in COURSE_CHANNELS"
            :key="c.id"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-pound"
            @click="go('chan', { chan: c.id, thing: null })"
            >{{ c.title }}<span v-if="S.unread[c.id]" class="ot-space__unread">{{ S.unread[c.id] }}</span></BaseButton
          >
        </div>
        <button
          v-for="l in recent"
          :key="l.id"
          type="button"
          class="ot-space__msg"
          @click="go('chan', { chan: l.chan, thing: null })"
        >
          <CheeseAvatar v-if="l.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="22" :name="NAMES[l.who]" :seed="l.who" />
          <span class="ot-space__who">{{ NAMES[l.who] }}</span>
          <span class="ot-space__in"># {{ channelOf(l.chan)?.title }}</span>
          <span class="ot-space__text">{{ plain(l.text) }}</span>
          <span class="ot-space__at">{{ whenText(l.at) }}</span>
        </button>
      </section>
    </div>
  </section>
</template>

<style scoped>
.ot-space {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-space__icon {
  color: var(--muted);
}

.ot-space__body {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 32px;
  overflow: auto;
}

.ot-space__sec + .ot-space__sec {
  margin-top: 28px;
}

.ot-space__sechead {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.ot-space__sechead h3 {
  margin: 0;
}

.ot-space__note {
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

.ot-space__row + .ot-space__row {
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
}

.ot-space__title {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.ot-space__type {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

.ot-space__about {
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-space__due {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  align-items: flex-end;
  width: 128px;
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
  width: 112px;
  font-size: 13px;
  color: var(--muted);
  text-align: right;
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

.ot-space__unread {
  margin-left: 6px;
  font-size: 12px;
  color: var(--muted);
}

.ot-space__msg {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  height: 36px;
  padding: 0 8px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-space__msg:hover {
  background: var(--fill);
}

.ot-space__who {
  flex: none;
  font-weight: 600;
  color: var(--ink);
}

.ot-space__in,
.ot-space__at {
  flex: none;
  font-size: 12px;
  color: var(--muted);
}

.ot-space__text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
