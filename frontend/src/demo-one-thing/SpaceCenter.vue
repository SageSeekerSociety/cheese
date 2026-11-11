<script setup lang="ts">
// The course 空间's pages, beside its sidebar. 题目 (all, or one 分类): each
// 题目 with its 分类, deadline, whether it is claimed alone (个人) or by a team
// (组队 and the team size), where I or my team stand, and 领取 or 进入. 领取
// makes a project owned by a team: a personal 题目 goes to my own team; one a
// team may claim asks 「自己领」 or 「和团队一起领」 and which team, and every
// member of that team is in the one project it gets. 公告 is the teacher's
// notices; 资料库 the slides and handouts. A space has no channels and holds
// no one's project.
import { computed, nextTick, ref, watch } from 'vue'

import { ANNOUNCEMENTS, dueChip, LIBRARY, modeText, topicById, TOPICS } from './course'
import { ME, NAMES, SPACE, TEAMS } from './data'
import { whenText } from './shapes'
import { claim, claimAs, S, standing, switchProject, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
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

// A 题目 shown from a project's 「来自」 line or a deadline: scrolled to and flashed.
const list = ref<HTMLElement | null>(null)
watch(
  () => S.spaceFlashTick,
  async () => {
    await nextTick()
    list.value?.querySelector(`[data-topic="${S.spaceFlash}"]`)?.scrollIntoView({ block: 'nearest' })
  }
)

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
  S.spacePage === 'announce' ? '公告' : S.spacePage === 'library' ? '资料库' : S.spaceCat ?? '全部题目'
)
</script>

<template>
  <section class="ot-space" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">{{ title }}</h2>
      <span class="ot-head__sub">{{ SPACE.name }} · {{ SPACE.org }} · {{ SPACE.teacher }}</span>
    </header>

    <div v-if="S.spacePage === 'tasks'" ref="list" class="ot-space__body">
      <p class="ot-space__note">
        领取后就是一个项目：个人题目建在你自己的团队下，组队题目建在领取它的团队下，组里每个人都在里面。一个团队一道题只有一个项目。
      </p>
      <div class="ot-space__list" data-zone="topics">
        <div
          v-for="t in rows"
          :key="`${t.id}-${S.spaceFlash === t.id ? S.spaceFlashTick : 0}`"
          class="ot-space__row"
          :class="{ 'ot-flash': S.spaceFlash === t.id && S.spaceFlashTick }"
          :data-topic="t.id"
        >
          <v-icon size="18" class="ot-space__kind" :icon="ICONS[t.category]" />
          <div class="ot-space__main">
            <span class="ot-space__title"
              >{{ t.title }}<span class="ot-space__type">{{ t.category }}</span></span
            >
            <span class="ot-space__about">{{ t.about }}</span>
          </div>
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
