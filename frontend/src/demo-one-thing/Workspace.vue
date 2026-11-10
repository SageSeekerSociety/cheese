<script setup lang="ts">
// The 1280×800 workspace: the product's project rail, the sidebar, the center
// column (always the channel, or 需要我的 / 项目文件 / 设置) and the right
// panel, which holds one selected thing: the open 一件事, or beside 项目文件
// the open file's 待定改动, 评测记录 and 历史. With nothing selected it is
// folded away. The rail holds the research project (where the tour runs), the
// course 空间 (it opens on its 题目 list), and each project 林晓 claimed from
// it, which appears on 领取; the other projects are inert.
// The scripted cursor, the notes overlay and the toast sit on top of it.
import type { MenuAction } from '@/components/common/menuAction'
import type { NavGenericItem } from '@/components/common/Navigation/types'

import { computed, ref } from 'vue'

import ChannelCenter from './ChannelCenter.vue'
import { CLAIMED, COURSE, ME, NAMES, OTHER_PROJECTS, PROJECT } from './data'
import FilesCenter from './FilesCenter.vue'
import FilesPanel from './FilesPanel.vue'
import InboxCenter from './InboxCenter.vue'
import SettingsCenter from './SettingsCenter.vue'
import SideNav from './SideNav.vue'
import SpaceCenter from './SpaceCenter.vue'
import { go, S, switchProject, thingById, toast, unreadIn } from './state'
import ThingPanel from './ThingPanel.vue'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import RailItem from '@/components/common/Navigation/RailItem.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

defineProps<{
  notes: {
    key: string
    x: number
    y: number
    w: number
    h: number
    title: string
    text: string
    nx: number
    ny: number
  }[]
}>()

const right = computed<'thing' | 'files' | null>(() => {
  if (S.center === 'files') return 'files'
  if (S.center === 'chan' && S.thing && thingById(S.thing)) return 'thing'
  return null
})

const wide = computed(() => right.value === 'thing' && thingById(S.thing)?.status !== 'talk')
const cols = computed(() => {
  if (right.value === 'thing' && S.focus) return '64px 228px 0px minmax(0, 1fr)'
  const r = right.value === null ? '0px' : right.value === 'files' ? '320px' : wide.value ? '560px' : '420px'
  return `64px 228px minmax(0, 1fr) ${r}`
})

const waiting = computed(() => S.things.filter((t) => t.status === 'wait' && t.owner === ME).length)

const rail = computed<NavGenericItem[]>(() => [
  {
    key: 'Home',
    type: 'item',
    title: '首页',
    icon: 'cheese',
    badge: 12 + waiting.value,
    action: () => go('inbox'),
    match: () => S.center === 'inbox',
  },
  { key: 'divider', type: 'divider' },
  {
    key: 'p-mm',
    type: 'item',
    title: PROJECT.name,
    projectId: 'p-mm',
    shortcut: 1,
    badge: unreadIn('research'),
    action: () => switchProject('research'),
    match: () => S.project === 'research',
  },
  {
    key: 'p-ics',
    type: 'item',
    title: COURSE.name,
    projectId: 'p-ics',
    shortcut: 2,
    badge: unreadIn('course'),
    action: () => switchProject('course'),
    match: () => S.project === 'course',
  },
  ...S.claimed.map((k, i) => ({
    key: CLAIMED[k].railId,
    type: 'item' as const,
    title: CLAIMED[k].name,
    projectId: CLAIMED[k].railId,
    shortcut: i + 3,
    badge: unreadIn(k),
    action: () => switchProject(k),
    match: () => S.project === k,
  })),
  ...OTHER_PROJECTS.map((p, i) => ({
    key: p.id,
    type: 'item' as const,
    title: p.name,
    projectId: p.id,
    shortcut: i + 3 + S.claimed.length,
    badge: p.badge,
    action: () => toast(`演示里能进的是「${PROJECT.name}」「${COURSE.name}」和从课程领取的项目`),
  })),
  {
    key: 'add',
    type: 'item',
    title: '新建项目',
    icon: 'mdi-plus',
    add: true,
    action: () => toast('新建项目：起个名字、选团队，就有了第一个频道「综合」'),
  },
])

const userOpen = ref(false)
const userActions: MenuAction[] = [
  { key: 'profile', label: '个人主页', icon: 'mdi-account-outline', onSelect: () => toast('个人主页') },
  {
    key: 'settings',
    label: '个人设置',
    icon: 'mdi-cog-outline',
    onSelect: () => go('settings', { settingsSection: 'me' }),
  },
  {
    key: 'usage',
    label: '用量',
    icon: 'mdi-chart-box-outline',
    onSelect: () => go('settings', { settingsSection: 'usage' }),
  },
  { key: 'out', label: '退出登录', icon: 'mdi-logout', divider: true, onSelect: () => toast('演示里不登录') },
]
</script>

<template>
  <div
    id="ot-app"
    class="ot-app"
    :class="{ 'ot-app--focus': S.focus && right === 'thing' }"
    :style="{ gridTemplateColumns: cols }"
  >
    <nav class="ot-rail app-rail" aria-label="项目">
      <div class="ot-rail__list">
        <RailItem v-for="item in rail" :key="item.key" :item="item" />
      </div>
      <div class="ot-rail__end">
        <button
          type="button"
          class="ot-rail__btn"
          title="问芝士"
          aria-label="问芝士"
          @click="toast('问芝士：项目之外的事也可以问它')"
        >
          <CheeseAvatar :size="32" name="芝士" handle="cheese" />
        </button>
        <AdaptiveMenu v-model="userOpen" :actions="userActions" location="top start" :title="NAMES[ME]">
          <template #activator="{ props: menuProps }">
            <button v-bind="menuProps" type="button" class="ot-rail__btn" :aria-label="NAMES[ME]" :title="NAMES[ME]">
              <UserAvatar :name="NAMES[ME]" :seed="ME" :size="32" />
            </button>
          </template>
        </AdaptiveMenu>
      </div>
    </nav>
    <SideNav />
    <main class="ot-pane">
      <ChannelCenter v-if="S.center === 'chan'" />
      <InboxCenter v-else-if="S.center === 'inbox'" />
      <FilesCenter v-else-if="S.center === 'files'" />
      <SpaceCenter v-else-if="S.center === 'space'" />
      <SettingsCenter v-else />
    </main>
    <div class="ot-right">
      <ThingPanel v-if="right === 'thing'" :key="S.thing ?? ''" />
      <FilesPanel v-else-if="right === 'files'" />
    </div>

    <div v-if="notes.length" class="ot-notes" aria-hidden="true">
      <template v-for="n in notes" :key="n.key">
        <div
          class="ot-notes__zone"
          :style="{ left: `${n.x}px`, top: `${n.y}px`, width: `${n.w}px`, height: `${n.h}px` }"
        />
        <div class="ot-notes__note" :style="{ left: `${n.nx}px`, top: `${n.ny}px` }">
          <b>{{ n.title }}</b
          >{{ n.text }}
        </div>
      </template>
    </div>
    <Transition name="ot-toast">
      <div v-if="S.toast" :key="S.toastTick" class="ot-toast" role="status">{{ S.toast }}</div>
    </Transition>
    <slot />
  </div>
</template>

<style scoped>
.ot-app {
  display: grid;
  position: relative;
  width: 1280px;
  height: 800px;
  overflow: hidden;
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  transition: grid-template-columns var(--dur-slow) var(--ease-standard);
}

.ot-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 0;
  padding: 8px 0 12px;
  background: var(--canvas);
}

.ot-rail__list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  min-height: 0;
}

.ot-rail__end {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
}

.ot-rail__btn {
  display: grid;
  place-items: center;
  padding: 0;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: 50%;
}

.ot-rail__btn:hover {
  opacity: 0.85;
}

/* Content area: one surface with a hairline against the canvas and a 12px corner at its top-left. */
.ot-pane {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
  border-left: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
}

.ot-app--focus .ot-pane {
  border: 0;
}

.ot-right {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
  border-left: 1px solid var(--line);
}

.ot-notes {
  position: absolute;
  inset: 0;
  z-index: var(--z-panel);
  pointer-events: none;
}

.ot-notes__zone {
  position: absolute;
  border: 2px dashed var(--ink);
  border-radius: var(--radius-md);
}

.ot-notes__note {
  position: absolute;
  max-width: 260px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--inverse-ink);
  background: var(--inverse-surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
}

.ot-notes__note b {
  display: block;
  margin-bottom: 2px;
}

/* The product's toasts drop in at the top centre, clear of the composer. */
.ot-toast {
  position: absolute;
  top: 16px;
  left: 50%;
  z-index: var(--z-shell);
  padding: 8px 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--inverse-ink);
  white-space: nowrap;
  pointer-events: none;
  background: var(--inverse-surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  transform: translateX(-50%);
}

.ot-toast-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.ot-toast-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.ot-toast-enter-from,
.ot-toast-leave-to {
  opacity: 0;
}
</style>
