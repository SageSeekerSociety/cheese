<script setup lang="ts">
// The 1280×800 workspace: the product's project rail, the sidebar, the center
// column (always the channel, or 需要我的 / 项目文件 / 设置) and the right
// panel, which holds one selected thing: the open 一件事, or beside 项目文件
// the open file's 待定改动, 历史 and 对话. With nothing selected it is
// folded away. The rail's first item, 首页, opens the home list (待办, my
// teams and their projects, the 空间 I belong to). Under it, each space I
// belong to has its own icon, Discord-server style: the course's color and
// initials with a cap mark, so it does not read as a project; it opens the same
// view as 首页's 空间 list does. Below a divider, the projects: the research
// project (where the tour runs), each project claimed from a 题目 (mine, and
// my team's), which appears on 领取, and the other projects, which are inert.
// A claimed project stays among the projects: it belongs to a team, not to
// the space.
// The 提交 dialog, the scripted cursor, the notes overlay and the toast sit on top of it.
import type { MenuAction } from '@/components/common/menuAction'
import type { NavGenericItem } from '@/components/common/Navigation/types'

import { computed, ref } from 'vue'

import { avatarColor } from '@/utils/avatar'

import ChannelCenter from './ChannelCenter.vue'
import { CLAIMED, ME, NAMES, OTHER_PROJECTS, PROJECT, SPACE } from './data'
import FilesCenter from './FilesCenter.vue'
import FilesPanel from './FilesPanel.vue'
import HomeSide from './HomeSide.vue'
import InboxCenter from './InboxCenter.vue'
import SettingsCenter from './SettingsCenter.vue'
import SideNav from './SideNav.vue'
import SpaceCenter from './SpaceCenter.vue'
import SpaceSide from './SpaceSide.vue'
import { deadlines, go, openHome, openSpace, S, switchProject, thingById, toast, unreadIn } from './state'
import SubmitDialog from './SubmitDialog.vue'
import TeamCenter from './TeamCenter.vue'
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
  if (S.area === 'home') return null
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

const waiting = computed(
  () => S.things.filter((t) => t.status === 'wait' && t.owner === ME).length + deadlines().length
)

const home = computed<NavGenericItem[]>(() => [
  {
    key: 'Home',
    type: 'item',
    title: '首页',
    icon: 'cheese',
    badge: waiting.value,
    action: () => openHome(),
    match: () => S.area === 'home' && S.center !== 'space',
  },
  { key: 'divider', type: 'divider' },
])

// The spaces I belong to (the demo has one). The product's rail tile only draws projects, so the
// space's tile is drawn here, in the rail's own tile classes (badge, the 「you are here」 bar, the flyout).
const spaces = computed(() => [
  {
    ...SPACE,
    color: avatarColor(String(SPACE.id)),
    // Its 题目 due within a week that are not done yet: the same deadlines 待办 lists.
    badge: deadlines().length,
    here: S.area === 'home' && S.center === 'space',
  },
])

const rail = computed<NavGenericItem[]>(() => [
  { key: 'divider-spaces', type: 'divider' },
  {
    key: 'p-mm',
    type: 'item',
    title: PROJECT.name,
    projectId: 'p-mm',
    shortcut: 1,
    badge: unreadIn('research'),
    action: () => switchProject('research'),
    match: () => S.area === 'project' && S.project === 'research',
  },
  ...S.claimed.map((k, i) => ({
    key: CLAIMED[k].railId,
    type: 'item' as const,
    title: CLAIMED[k].name,
    projectId: CLAIMED[k].railId,
    shortcut: i + 2,
    badge: unreadIn(k),
    action: () => switchProject(k),
    match: () => S.area === 'project' && S.project === k,
  })),
  ...OTHER_PROJECTS.map((p, i) => ({
    key: p.id,
    type: 'item' as const,
    title: p.name,
    projectId: p.id,
    shortcut: i + 2 + S.claimed.length,
    badge: p.badge,
    action: () => toast(`演示里能进的是「${PROJECT.name}」和从空间领取的项目`),
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
        <RailItem v-for="item in home" :key="item.key" :item="item" />
        <button
          v-for="sp in spaces"
          :key="sp.id"
          type="button"
          class="app-rail-item ot-rail__space"
          :aria-label="sp.badge ? `${sp.name}（${sp.badge} 个快截止）` : sp.name"
          :aria-current="sp.here ? 'page' : undefined"
          :data-space="sp.id"
          @click="openSpace('tasks')"
        >
          <v-tooltip activator="parent" location="end" content-class="rail-flyout">
            <div class="rail-flyout__inner">
              <span class="rail-flyout__name">{{ sp.name }}</span>
            </div>
          </v-tooltip>
          <span class="ot-rail__space-face" :style="{ background: sp.color }">{{ sp.short }}</span>
          <span class="ot-rail__space-mark" aria-hidden="true"><v-icon size="11" icon="mdi-school" /></span>
          <span v-if="sp.badge" class="app-rail-item__badge" aria-hidden="true">{{ sp.badge }}</span>
        </button>
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
    <SpaceSide v-if="S.area === 'home' && S.center === 'space'" />
    <HomeSide v-else-if="S.area === 'home'" />
    <SideNav v-else />
    <main class="ot-pane">
      <ChannelCenter v-if="S.center === 'chan'" />
      <InboxCenter v-else-if="S.center === 'inbox'" />
      <FilesCenter v-else-if="S.center === 'files'" />
      <SpaceCenter v-else-if="S.center === 'space'" />
      <TeamCenter v-else-if="S.center === 'team'" />
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
    <SubmitDialog />
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

/* A space's tile: the same 48px slot and 「you are here」 bar as a project's, but its face is the
   course's color with its initials, a rounder square, and a cap mark in the corner. */
.app-rail-item.ot-rail__space,
.app-rail-item.ot-rail__space:hover {
  padding: 0;
  font: inherit;
  background: none;
  border: 0;
  border-radius: 16px;
}

.ot-rail__space-face {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--avatar-ink);
  border-radius: 16px;
  transition: border-radius var(--dur-quick) var(--ease-standard);
}

.ot-rail__space:hover .ot-rail__space-face,
.ot-rail__space[aria-current] .ot-rail__space-face {
  border-radius: 12px;
}

.ot-rail__space-mark {
  display: grid;
  position: absolute;
  right: -3px;
  bottom: -3px;
  place-items: center;
  width: 18px;
  height: 18px;
  color: var(--ink);
  background: var(--surface);
  border-radius: 50%;
  box-shadow: 0 0 0 2px var(--canvas);
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
