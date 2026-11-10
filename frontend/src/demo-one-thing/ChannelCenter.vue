<script setup lang="ts">
// The center column while a channel is open: the product's channel header row
// (title and description, 摘要 / 对话, the roster, the bell, 进行中, ⋯), then
// either 芝士's 摘要 of the channel or the conversation and its composer. The
// title opens 频道详情 as a popover; the right panel stays folded until a 一件事
// is opened, from a card, from 摘要 or from the 进行中 menu.
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectMemberRow, TopicMemberRow, TopicNotifyLevel } from '@/cx_types'
import type { ChatLine } from './state'

import { computed, ref, watch } from 'vue'

import ChannelDetails from './ChannelDetails.vue'
import ChannelSummary from './ChannelSummary.vue'
import { CHANNELS, ME, NAMES, PEOPLE } from './data'
import DemoComposer from './DemoComposer.vue'
import DemoTimeline from './DemoTimeline.vue'
import { type ChanView, openThing, S, send, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import ChannelNotifyMenu from '@/components/room/ChannelNotifyMenu.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import { answer } from '@/views/demo/demoBackend'

const channel = computed(() => CHANNELS.find((c) => c.id === S.chan)!)
const lines = computed(() => S.chat[S.chan] ?? [])
const view = computed<ChanView>({
  get: () => S.chanView[S.chan] ?? 'talk',
  set: (v) => (S.chanView[S.chan] = v),
})
const VIEWS = [
  { value: 'summary' as const, label: '摘要' },
  { value: 'talk' as const, label: '对话' },
]

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
  // The menu closes on this same click; open the popover after, or its outside-click handler sees the click and shuts it.
  {
    key: 'details',
    label: '频道详情',
    icon: 'mdi-information-outline',
    onSelect: () => void setTimeout(() => (S.chanDetails = true), 60),
  },
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

const STATUS_TEXT = { talk: '讨论中', run: '在做', wait: '等采纳', done: '已完成' } as const
const busy = computed(() => S.things.filter((t) => t.chan === S.chan && t.status !== 'done'))
const busyOpen = ref(false)
const busyActions = computed<MenuAction[]>(() => {
  const done = S.things.filter((t) => t.chan === S.chan && t.status === 'done')
  return [...busy.value, ...done].map((t, i) => ({
    key: t.id,
    label: `${t.title} · ${STATUS_TEXT[t.status]}`,
    icon: t.status === 'done' ? 'mdi-check' : t.status === 'talk' ? 'mdi-forum-outline' : 'mdi-checkbox-marked-outline',
    divider: i === busy.value.length && i > 0,
    onSelect: () => openThing(t.id),
  }))
})

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
  () => {
    S.replyTo = null
    S.chanDetails = false
  }
)
</script>

<template>
  <section class="ot-chan" data-zone="center">
    <header class="topic-header">
      <!-- With a 一件事 open the column is narrow and the description gives way first; the title never does. -->
      <div class="topic-header__text" :style="{ minWidth: `${channel.title.length + 1}em` }">
        <v-menu v-model="S.chanDetails" :close-on-content-click="false" location="bottom start" :offset="6">
          <template #activator="{ props: menuProps }">
            <button v-bind="menuProps" type="button" class="topic-header__title t-title" title="频道详情">
              {{ channel.title }}
            </button>
          </template>
          <ChannelDetails />
        </v-menu>
        <span class="topic-header__description" :title="channel.about">{{ channel.about }}</span>
      </div>
      <SegmentedControl v-model="view" :options="VIEWS" label="频道视图" class="topic-header__views" />
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
      <AdaptiveMenu v-if="busy.length" v-model="busyOpen" :actions="busyActions" title="这个频道的事">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-checkbox-marked-outline"
            title="这个频道里进行中的事"
          >
            进行中 {{ busy.length }}
          </BaseButton>
        </template>
      </AdaptiveMenu>
      <AdaptiveMenu v-model="menuOpen" :actions="actions" title="更多">
        <template #activator="{ props: menuProps }">
          <BaseButton v-bind="menuProps" icon="mdi-dots-horizontal" size="sm" title="更多" aria-label="更多" />
        </template>
      </AdaptiveMenu>
    </header>
    <ChannelSummary v-if="view === 'summary'" :key="S.chan" class="ot-chan__summary" />
    <div v-else class="ot-chan__lines">
      <DemoTimeline :lines="lines" :conversation="S.chan" cards @reply="onReply" />
    </div>
    <DemoComposer
      v-if="view === 'talk'"
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

.topic-header__views {
  flex: none;
}

.ot-chan__summary {
  flex: 1;
  min-height: 0;
}

.ot-chan__lines {
  flex: 1;
  min-height: 0;
  padding: 8px 0;
  overflow: auto;
}
</style>
