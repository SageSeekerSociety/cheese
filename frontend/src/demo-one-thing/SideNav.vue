<script setup lang="ts">
// The project sidebar: the product's project header (the team that owns it,
// project menu, search), 需要我的 on top, the channels in a fixed order, then
// 项目文件, and 设置 at the bottom. Rows are the product's own sidebar rows. A
// project claimed from a 题目 is an ordinary project and gets the same sidebar,
// with one card under the header: the 题目 it came from. Its name opens that
// 题目 in the space; under it the deadline, how it is graded, where my (or my
// team's) latest submission stands, and 「提交」. Submitting is the 题目's
// business, so it lives here and not in the code editor, which is the same in
// every project.
import type { MenuCommand } from '@/commands'
import type { Topic } from '@/cx_types'
import type { VisibleRow } from '@/lib/topicTree'

import { computed, ref } from 'vue'

import { dueChip, GRADING_TEXT, topicOf } from './course'
import { type Channel, channelsOf, isClaim, projectMeta, SPACE } from './data'
import { deadlines, go, myStatus, openSubmit, S, showTopic, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailHeader from '@/components/topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
import i18n from '@/i18n'
import { monthDayTime } from '@/lib/dateText'

const TERMS = { project: '项目', topic: '频道' }

// The pinned rows translate their labels; the demo's three live under its own keys.
i18n.global.mergeLocaleMessage('zh-CN', {
  demoOneThing: { inbox: '需要我的', files: '项目文件', settings: '设置' },
})

const MENU_PAGES = [
  { key: 'project-tasks', label: 'navigation.project.tasks', icon: 'mdi-format-list-checks' },
  { key: 'project-members', label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
  { key: 'project-routines', label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' },
  { key: 'project-skills', label: 'navigation.project.skills', icon: 'mdi-book-cog-outline' },
]

const space = computed(() => projectMeta(S.project))
/** The 题目 this project was claimed from (the product records it as the project's external_task_id). */
const origin = computed(() => (isClaim(S.project) ? topicOf(S.project) : null))
const mine = computed(() => (origin.value ? myStatus(origin.value.id) : null))
const channels = computed(() => channelsOf(S.project))

// 需要我的: every 一件事 waiting on me, and every deadline across my projects.
const waiting = computed(
  () => S.things.filter((t) => t.status === 'wait' && t.owner === 'linxiao').length + deadlines().length
)
const filesPending = computed(() => (S.project !== 'research' || S.suggAccepted || S.suggRejected ? 0 : 1))

function row(c: Channel): VisibleRow<Topic> {
  return {
    topic: {
      id: c.id,
      project_id: 'p1',
      parent_id: null,
      title: c.title,
      kind: c.id === 'c-general' ? 'root' : 'topic',
      status: 'active',
      created_at: '2026-11-01T00:00:00Z',
    } as Topic,
    depth: 0,
    hasChildren: false,
    collapsed: false,
    hiddenCount: 0,
    hiddenUnread: 0,
    unreadTotal: S.unread[c.id] ?? 0,
    hiddenLight: null,
    hiddenStalled: false,
  }
}

const menuFor = ref<string | null>(null)
function actions(topic: Topic): MenuCommand[] {
  const unread = !!S.unread[topic.id]
  return [
    {
      id: 'read',
      title: '标为已读',
      icon: 'mdi-check-all',
      disabled: !unread,
      run: () => {
        delete S.unread[topic.id]
      },
    },
    { id: 'mute', title: '静音这个频道', icon: 'mdi-bell-off-outline', run: () => toast(`「${topic.title}」静音了`) },
    {
      id: 'details',
      title: '频道详情',
      icon: 'mdi-information-outline',
      run: () => {
        go('chan', { chan: topic.id, thing: null })
        // After the row menu has closed, so its outside click does not shut the popover.
        setTimeout(() => (S.chanDetails = true), 60)
      },
    },
    { id: 'link', title: '复制链接', icon: 'mdi-link-variant', run: () => toast('已复制链接') },
  ]
}

function openPage(key: string) {
  if (key === 'inbox' || key === 'files' || key === 'settings') go(key)
  else if (key === 'project-settings') go('settings', { settingsSection: 'project' })
  else if (key === 'project-members') go('settings', { settingsSection: 'members' })
  else toast('演示里只有频道、项目文件和设置这几页')
}
</script>

<template>
  <nav class="ot-side" data-zone="side">
    <TopicRailHeader
      :page="false"
      :column="false"
      :project-name="space.name"
      :team="{ label: space.team, handle: space.teamHandle }"
      :private-unread-total="0"
      search-title="搜索和跳转（⌘K）"
      :menu-open="false"
      :menu-pages="MENU_PAGES"
      :docs-active="false"
      :route-name="S.center === 'settings' ? 'project-settings' : null"
      :terms="TERMS"
      :project-selected="true"
      :can-transfer="true"
      :can-leave="false"
      @open-page="openPage"
      @select-docs="go('files', { ftab: 'history' })"
      @open-palette="toast('⌘K：搜索频道、文件和一件事')"
      @open-transfer="toast('演示里不能转让项目')"
    />

    <section v-if="origin && mine" class="ot-orig" data-origin aria-label="来自的题目">
      <div class="ot-orig__space"><v-icon size="13" icon="mdi-school-outline" />{{ SPACE.name }}</div>
      <div class="ot-orig__head">
        <button
          type="button"
          class="ot-orig__title"
          :title="`在空间里打开「${origin.title}」`"
          data-origin-topic
          @click="showTopic(origin.id)"
        >
          <span>{{ origin.title }}</span
          ><v-icon size="14" icon="mdi-chevron-right" />
        </button>
        <span class="ot-orig__mode">{{ GRADING_TEXT[origin.grading] }}</span>
      </div>
      <div class="ot-orig__due">
        截止 {{ monthDayTime(Date.parse(origin.due)) }}<b v-if="dueChip(origin.due)"> · {{ dueChip(origin.due) }}</b>
      </div>
      <div class="ot-orig__foot">
        <span class="ot-orig__st" :class="`is-${mine.tone}`" data-origin-status>
          <b>{{ mine.text }}</b>
          <span v-if="mine.sub">{{ mine.sub }}</span>
        </span>
        <BaseButton
          id="b-submit"
          kind="primary"
          size="sm"
          prepend-icon="mdi-upload-outline"
          @click="openSubmit(origin.id)"
          >提交</BaseButton
        >
      </div>
    </section>

    <div class="ot-side__scroll">
      <div class="ot-side__row" data-nav="inbox">
        <TopicRailPinnedRows
          :pages="[{ key: 'inbox', label: 'demoOneThing.inbox', icon: 'mdi-inbox-arrow-down-outline' }]"
          :route-name="S.center"
          :terms="TERMS"
          :page="false"
          @open-page="openPage"
        />
        <TopicRailBadge v-if="waiting" class="ot-side__badge" :count="waiting" />
      </div>

      <div class="ot-side__sec">
        <span class="t-eyebrow-read">频道</span>
        <button
          type="button"
          class="ot-side__add"
          title="新建频道"
          aria-label="新建频道"
          @click="toast('新建频道：起个名字，选谁能进')"
        >
          <v-icon size="16" icon="mdi-plus" />
        </button>
      </div>
      <div v-for="c in channels" :key="c.id" class="ot-side__row" :data-chan="c.id">
        <TopicRailRow
          :row="row(c)"
          :selected="S.center === 'chan' && S.chan === c.id"
          :page="false"
          :renaming="false"
          :menu-open="menuFor === c.id"
          :stalled="false"
          toggle-title="收起"
          :light="null"
          :actions="actions"
          :fresh="!!S.unread[c.id]"
          @update:menu-open="(open: boolean) => (menuFor = open ? c.id : null)"
          @select="go('chan', { chan: c.id, thing: null })"
        />
        <TopicRailBadge
          v-if="S.unread[c.id] && menuFor !== c.id"
          class="ot-side__badge ot-side__badge--quiet"
          :count="S.unread[c.id]!"
        />
      </div>

      <div class="ot-side__sec"><span class="t-eyebrow-read">项目</span></div>
      <div class="ot-side__row" data-nav="files">
        <TopicRailPinnedRows
          :pages="[{ key: 'files', label: 'demoOneThing.files', icon: 'mdi-folder-outline' }]"
          :route-name="S.center"
          :terms="TERMS"
          :page="false"
          @open-page="openPage"
        />
        <TopicRailBadge v-if="filesPending" class="ot-side__badge ot-side__badge--quiet" :count="filesPending" />
      </div>
    </div>

    <div class="ot-side__row ot-side__foot" data-nav="settings">
      <TopicRailPinnedRows
        :pages="[{ key: 'settings', label: 'demoOneThing.settings', icon: 'mdi-cog-outline' }]"
        :route-name="S.center"
        :terms="TERMS"
        :page="false"
        @open-page="openPage"
      />
    </div>
  </nav>
</template>

<style scoped>
.ot-side {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--canvas);
}

/* The 题目 this project came from: one compact card, its name leading back to the space. */
.ot-orig {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 8px 8px 0;
  padding: 8px 10px 10px;
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ot-orig__space {
  display: flex;
  gap: 4px;
  align-items: center;
  overflow: hidden;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: nowrap;
}

.ot-orig__head {
  display: flex;
  gap: 6px;
  align-items: center;
}

.ot-orig__title {
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

.ot-orig__title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-orig__title .v-icon {
  flex: none;
  color: var(--muted);
}

.ot-orig__title:hover span {
  text-decoration: underline;
}

.ot-orig__mode {
  flex: none;
  padding: 0 6px;
  font-size: 12px;
  line-height: 18px;
  color: var(--muted);
  background: var(--surface);
  border-radius: var(--radius-sm);
}

.ot-orig__due {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

/* Due soon: the same amber as anything else that waits on me. */
.ot-orig__due b {
  font-weight: 400;
  color: var(--accent-ink);
}

.ot-orig__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

.ot-orig__st {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-orig__st b {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.ot-orig__st.is-ok b {
  color: var(--ok-ink);
}

.ot-orig__st.is-bad b,
.ot-orig__st.is-wait b {
  color: var(--ink);
}

.ot-side__scroll {
  flex: 1;
  min-height: 0;
  padding: 8px 8px 0;
  overflow: auto;
}

.ot-side__sec {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 8px 4px;
}

.ot-side__add {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-side__add:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-side__row {
  position: relative;
}

/* The product row keeps its own trailing slot for the ⋯ button; the count sits just left of it. */
.ot-side__badge {
  position: absolute;
  top: 50%;
  right: 12px;
  transform: translateY(-50%);
  pointer-events: none;
}

.ot-side__row:hover .ot-side__badge--quiet {
  visibility: hidden;
}

/* A channel's count is not the amber "等你" signal: only 需要我的 carries that. */
.ot-side__row .ot-side__badge--quiet {
  color: var(--muted);
}

.ot-side :deep(.v-list) {
  background: transparent;
}

.ot-side__foot {
  padding: 4px 8px 8px;
  border-top: 1px solid var(--line);
}
</style>
