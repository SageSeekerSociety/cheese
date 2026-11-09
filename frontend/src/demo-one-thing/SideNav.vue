<script setup lang="ts">
// The project sidebar: 需要我的 on top, the channels in a fixed order, then
// 项目文件, and 设置 at the bottom. Rows are the product's own sidebar rows.
import type { Topic } from '@/cx_types'
import type { VisibleRow } from '@/lib/topicTree'

import { computed } from 'vue'

import { CHANNELS, ME, NAMES, PROJECT } from './data'
import { go, S } from './state'

import UserAvatar from '@/components/common/UserAvatar.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
import i18n from '@/i18n'

const TERMS = { project: '项目', topic: '频道' }

// The pinned rows translate their labels; the demo's three live under its own keys.
i18n.global.mergeLocaleMessage('zh-CN', { demoOneThing: { inbox: '需要我的', files: '项目文件', settings: '设置' } })

const waiting = computed(() => S.things.filter((t) => t.status === 'wait').length)
const filesPending = computed(() => (S.suggAccepted ? 0 : 1))

function row(c: (typeof CHANNELS)[number]): VisibleRow<Topic> {
  return {
    topic: {
      id: c.id,
      project_id: 'p1',
      parent_id: null,
      title: c.title,
      kind: 'topic',
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

function openPage(key: string) {
  if (key === 'inbox' || key === 'files' || key === 'settings') go(key)
}
</script>

<template>
  <nav class="ot-side" data-zone="side">
    <div class="ot-side__project">
      <span class="ot-side__team">{{ PROJECT.team }}</span>
      <span class="ot-side__name">{{ PROJECT.name }}</span>
    </div>

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

    <div class="ot-side__sec t-eyebrow-read">频道</div>
    <div v-for="c in CHANNELS" :key="c.id" class="ot-side__row" :data-chan="c.id">
      <TopicRailRow
        :row="row(c)"
        :selected="S.center === 'chan' && S.chan === c.id"
        :page="false"
        :renaming="false"
        :menu-open="false"
        :stalled="false"
        toggle-title="收起"
        :light="null"
        :actions="() => []"
        :fresh="!!S.unread[c.id]"
        @select="go('chan', { chan: c.id })"
      />
      <TopicRailBadge v-if="S.unread[c.id]" class="ot-side__badge ot-side__badge--quiet" :count="S.unread[c.id]!" />
    </div>

    <div class="ot-side__sec t-eyebrow-read">项目</div>
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

    <div class="ot-side__fill" />
    <div class="ot-side__row" data-nav="settings">
      <TopicRailPinnedRows
        :pages="[{ key: 'settings', label: 'demoOneThing.settings', icon: 'mdi-cog-outline' }]"
        :route-name="S.center"
        :terms="TERMS"
        :page="false"
        @open-page="openPage"
      />
    </div>
    <div class="ot-side__me">
      <UserAvatar :name="NAMES[ME]" :seed="ME" :size="24" />
      <span>{{ NAMES[ME] }}</span>
    </div>
  </nav>
</template>

<style scoped>
.ot-side {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 0 8px;
  background: var(--canvas);
}

.ot-side__project {
  display: flex;
  flex-direction: column;
  justify-content: center;
  height: 48px;
  padding: 0 8px;
  margin-bottom: 8px;
}

.ot-side__team {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-side__name {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ot-side__sec {
  padding: 12px 8px 4px;
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

/* A channel's count is not the amber "等你" signal: only 需要我的 carries that. */
.ot-side__row .ot-side__badge--quiet {
  color: var(--muted);
}

.ot-side :deep(.v-list) {
  background: transparent;
}

.ot-side__fill {
  flex: 1;
}

.ot-side__me {
  display: flex;
  gap: 8px;
  align-items: center;
  height: 48px;
  padding: 0 8px;
  font-size: 13px;
  color: var(--ink);
  border-top: 1px solid var(--line);
}
</style>
