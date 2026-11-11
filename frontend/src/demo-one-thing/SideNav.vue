<script setup lang="ts">
// The project sidebar: the product's project header (the team that owns it,
// project menu, search), 需要我的 on top, the channels in a fixed order, then
// 项目文件, and 设置 at the bottom. Rows are the product's own sidebar rows. A
// project claimed from a 题目 is an ordinary project and gets the same sidebar,
// with one quiet line under the header leading back to its 题目 and to 提交
// (OriginCard.vue). The code editor has no 提交: it is the same in every project.
import type { MenuCommand } from '@/commands'
import type { Topic } from '@/cx_types'
import type { VisibleRow } from '@/lib/topicTree'

import { computed, ref } from 'vue'

import { topicOf } from './course'
import { type Channel, channelsOf, isClaim, projectMeta } from './data'
import OriginCard from './OriginCard.vue'
import { deadlines, go, S, toast } from './state'

import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailHeader from '@/components/topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
import i18n from '@/i18n'

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
      @select-docs="go('files')"
      @open-palette="toast('⌘K：搜索频道、文件和一件事')"
      @open-transfer="toast('演示里不能转让项目')"
    />

    <OriginCard v-if="origin" />

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
