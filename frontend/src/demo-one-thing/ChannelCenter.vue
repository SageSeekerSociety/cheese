<script setup lang="ts">
// The center column while a channel is open: the product's channel header row
// (title and description, the roster, the bell, 概览, ⋯), the conversation and
// the composer.
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectMemberRow, TopicMemberRow, TopicNotifyLevel } from '@/cx_types'
import type { ChatLine } from './state'

import { computed, ref, watch } from 'vue'

import { CHANNELS, ME, NAMES, PEOPLE } from './data'
import DemoComposer from './DemoComposer.vue'
import DemoTimeline from './DemoTimeline.vue'
import { S, send, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import ChannelNotifyMenu from '@/components/room/ChannelNotifyMenu.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import { answer } from '@/views/demo/demoBackend'

const channel = computed(() => CHANNELS.find((c) => c.id === S.chan)!)
const lines = computed(() => S.chat[S.chan] ?? [])

// The roster the header reads comes from the room's members endpoint: answer it
// for every channel, so the product's own TopicMembers draws the avatar stack.
for (const c of CHANNELS) {
  const rows: TopicMemberRow[] = [...c.members, 'cheese'].map((h, i) => ({
    topic_id: c.id,
    member_handle: h,
    role: i === 0 ? 'owner' : 'member',
    name: NAMES[h],
    agent: h === 'cheese',
    instance_handle: h === 'cheese' ? 'cheese' : undefined,
    avatar_id: null,
  }))
  answer(`/topics/${c.id}/members`, () => ({ data: rows, total: rows.length }))
}
const projectMembers: ProjectMemberRow[] = PEOPLE.map((p) => ({
  user_handle: p.handle,
  name: p.name,
  source: p.agent ? 'agent' : p.handle === ME ? 'owner' : 'team',
}))

const notify = ref<Record<string, { level: TopicNotifyLevel; until: string | null }>>({})
const level = computed(() => notify.value[S.chan] ?? { level: 'all' as TopicNotifyLevel, until: null })
function setNotify(l: TopicNotifyLevel, until: string | null) {
  notify.value = { ...notify.value, [S.chan]: { level: l, until } }
  toast(l === 'mute' ? '这个频道静音了' : l === 'all' ? '每条消息都会通知你' : '只在 @ 你时通知')
}

const membersOpen = ref(false)
const menuOpen = ref(false)
const actions = computed<MenuAction[]>(() => [
  { key: 'details', label: '频道详情', icon: 'mdi-information-outline', onSelect: () => (S.chanPanel = true) },
  { key: 'pins', label: '置顶的消息', icon: 'mdi-pin-outline', onSelect: () => toast('这个频道还没有置顶的消息') },
  { key: 'search', label: '在频道里搜索', icon: 'mdi-magnify', onSelect: () => toast('⌘K 搜索消息、文件和一件事') },
  { key: 'link', label: '复制频道链接', icon: 'mdi-link-variant', onSelect: () => toast('已复制链接') },
  {
    key: 'leave',
    label: '退出频道',
    icon: 'mdi-exit-to-app',
    danger: true,
    divider: true,
    onSelect: () => toast('演示里不能退出频道'),
  },
])

const replyLine = computed(() => lines.value.find((l) => l.id === S.replyTo) ?? null)
const replyLabel = computed(() =>
  replyLine.value
    ? `回复 ${NAMES[replyLine.value.who]}：${replyLine.value.text.replace(/<@cheese>/g, '@芝士').slice(0, 40)}`
    : null
)
function onReply(line: ChatLine) {
  S.replyTo = line.id
}
watch(
  () => S.chan,
  () => (S.replyTo = null)
)
</script>

<template>
  <section class="ot-chan" data-zone="center">
    <header class="topic-header">
      <div class="topic-header__text">
        <button type="button" class="topic-header__title t-title" :title="channel.title" @click="S.chanPanel = true">
          {{ channel.title }}
        </button>
        <span class="topic-header__description" :title="channel.about">{{ channel.about }}</span>
      </div>
      <TopicMembers
        :key="S.chan"
        v-model:open="membersOpen"
        :topic-id="S.chan"
        project-id="demo"
        :project-members="projectMembers"
        :me="ME"
        :can-manage="true"
        :can-invite="true"
        :joined="true"
        :general="S.chan === 'c-general'"
      />
      <ChannelNotifyMenu :level="level.level" :muted-until="level.until" @set="setNotify" />
      <PanelToggle
        :open="S.chanPanel || !!S.thing"
        @toggle="S.chanPanel || S.thing ? ((S.thing = null), (S.chanPanel = false)) : (S.chanPanel = true)"
      />
      <AdaptiveMenu v-model="menuOpen" :actions="actions" title="更多">
        <template #activator="{ props: menuProps }">
          <BaseButton v-bind="menuProps" icon="mdi-dots-horizontal" size="sm" title="更多" aria-label="更多" />
        </template>
      </AdaptiveMenu>
    </header>
    <div class="ot-chan__lines">
      <DemoTimeline :lines="lines" :conversation="S.chan" cards @reply="onReply" />
    </div>
    <DemoComposer
      v-model="S.typing"
      placeholder="输入消息，@芝士 交给它处理"
      :reply-label="replyLabel"
      @clear-reply="S.replyTo = null"
      @send="send"
    />
  </section>
</template>

<style scoped>
.ot-chan {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.topic-header {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  height: var(--app-page-header-height, 48px);
  padding: 0 12px 0 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.topic-header__text {
  display: flex;
  flex: 1 1 auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.topic-header__title {
  flex: none;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  color: var(--ink);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border: 0;
}

.topic-header__title:hover {
  color: var(--accent-ink);
}

.topic-header__description {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-chan__lines {
  flex: 1;
  min-height: 0;
  padding: 8px 0;
  overflow: auto;
}
</style>
