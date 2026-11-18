<template>
  <!-- 侧栏头：老鼠图标（回首页）+ 当前项目（点开换项目）。左边那一列方块栏拿掉后
       （appNav.ts），首页和项目都从这一行进。 -->
  <div class="sidebar-header frame-top" data-testid="sidebar-frame-top">
    <RailItem v-if="homeItem" :item="homeItem" class="frame-top__home" />

    <v-menu v-model="open" location="bottom start" :offset="6" :close-on-content-click="false">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          type="button"
          class="frame-top__switch"
          :class="{ 'frame-top__switch--open': open }"
          data-testid="project-switcher"
          :title="current ? current.title : t('work.sidebar.switchProject')"
          :aria-label="t('work.sidebar.switchProject')"
          aria-haspopup="menu"
        >
          <span class="frame-top__text">
            <span class="frame-top__name" data-user-content>{{
              current ? current.title : t('work.sidebar.chooseProject')
            }}</span>
            <router-link
              v-if="team"
              class="frame-top__team"
              :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }"
              data-user-content
              @click.stop
              >{{ team.label }}</router-link
            >
          </span>
          <TopicRailBadge
            v-if="othersAwaiting > 0"
            :count="othersAwaiting"
            :title="t('navigation.switcher.othersAwaiting', { n: othersAwaiting })"
          />
          <v-icon class="frame-top__caret" size="18" icon="mdi-chevron-down" />
        </button>
      </template>

      <div class="switcher">
        <label class="switcher__search">
          <v-icon size="16" icon="mdi-magnify" />
          <input
            ref="searchInput"
            v-model="query"
            type="search"
            data-testid="project-switcher-search"
            :placeholder="t('navigation.switcher.search')"
            :aria-label="t('navigation.switcher.search')"
            autocomplete="off"
            @keydown.enter.prevent="openFirst"
          />
        </label>
        <div class="switcher__list">
          <router-link
            v-if="homeItem && !query"
            :to="homeItem.to ?? '/inbox'"
            class="switcher__row"
            :aria-current="onHome ? 'page' : undefined"
            @click="open = false"
          >
            <span class="switcher__home"><CheeseLogo width="14" height="14" /></span>
            <span class="switcher__label">{{ homeItem.title }}</span>
            <TopicRailBadge v-if="homeItem.badge" :count="homeItem.badge" />
            <kbd v-if="homeItem.shortcut" class="switcher__kbd">{{ keyLabel(homeItem.shortcut) }}</kbd>
          </router-link>
          <div v-if="homeItem && !query && rows.length" class="switcher__rule" />
          <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- drag handlers for reordering with the pointer; keyboard users reorder nothing here, same as the old rail -->
          <div
            v-for="row in rows"
            :key="row.item.key"
            class="switcher__row-wrap"
            :class="{
              'switcher__row-wrap--dragging': draggingId === row.item.projectId,
              'switcher__row-wrap--before': edgeFor(row.item.projectId) === 'before',
              'switcher__row-wrap--after': edgeFor(row.item.projectId) === 'after',
            }"
            :draggable="!query"
            :data-project-id="row.item.projectId"
            @dragstart="onDragStart($event, row.item.projectId!)"
            @dragover="onDragOver($event, row.item.projectId!)"
            @drop="onDrop($event, row.item.projectId!)"
            @dragend="clearDrag"
          >
            <router-link
              :to="row.item.to ?? '/'"
              class="switcher__row"
              :aria-current="row.current ? 'page' : undefined"
              data-testid="project-switcher-row"
              @click="open = false"
            >
              <v-icon v-if="!query" class="switcher__grip" size="14" icon="mdi-drag-vertical" />
              <ProjectAvatar
                :id="row.item.projectId ?? ''"
                :name="row.item.title"
                :avatar="row.item.avatar"
                :size="20"
              />
              <span class="switcher__label" :title="row.item.title" data-user-content>{{ row.item.title }}</span>
              <span v-if="row.team" class="switcher__aside" data-user-content>{{ row.team }}</span>
              <TopicRailBadge v-if="row.item.badge" :count="row.item.badge" />
              <kbd v-if="row.item.shortcut" class="switcher__kbd">{{ keyLabel(row.item.shortcut) }}</kbd>
            </router-link>
            <AdaptiveMenu v-if="row.item.menu?.length" :actions="row.item.menu" :title="row.item.title">
              <template #activator="{ props: actProps }">
                <button
                  v-bind="actProps"
                  type="button"
                  class="switcher__more"
                  :aria-label="t('navigation.switcher.actions', { name: row.item.title })"
                >
                  <v-icon size="16" icon="mdi-dots-horizontal" />
                </button>
              </template>
            </AdaptiveMenu>
          </div>
          <div v-if="query && !rows.length" class="switcher__empty">
            {{ t('navigation.switcher.empty', { q: query }) }}
          </div>
        </div>
        <div class="switcher__rule" />
        <button v-if="addItem" type="button" class="switcher__row" @click="createProject">
          <v-icon size="16" icon="mdi-plus" class="switcher__icon" />
          <span class="switcher__label">{{ addItem.title }}</span>
        </button>
        <router-link :to="{ name: 'my-archived-projects' }" class="switcher__row" @click="open = false">
          <v-icon size="16" icon="mdi-archive-outline" class="switcher__icon" />
          <span class="switcher__label">{{ t('navigation.userMenu.archivedProjects') }}</span>
        </router-link>
      </div>
    </v-menu>
  </div>
</template>

<script setup lang="ts">
import type { DropEdge } from '@/lib/projectOrder'

import { computed, nextTick, ref, watch } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { addItemOf, homeItemOf, projectItemsOf, useAppNav } from './appNav'
import { railShortcut } from './destinations'
import RailItem from './RailItem.vue'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import ProjectAvatar from '@/components/common/ProjectAvatar.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import { t } from '@/i18n'
import { inDesktopApp } from '@/lib/desktopApp'
import { myHandle } from '@/me'

const nav = useAppNav()
const navigation = useNavigation()

const items = computed(() => nav?.items.value ?? [])
const homeItem = computed(() => homeItemOf(items.value))
const addItem = computed(() => addItemOf(items.value))
const projectItems = computed(() => projectItemsOf(items.value))

const path = computed(() => navigation?.route?.path ?? '')
const current = computed(() => projectItems.value.find((i) => i.match?.(path.value)) ?? null)
const onHome = computed(() => !!homeItem.value?.match?.(path.value))
// 只在项目里算：在首页时老鼠图标上那个数就是全部，这里再写一遍是重复。
const othersAwaiting = computed(() =>
  current.value ? projectItems.value.filter((i) => i !== current.value).reduce((sum, i) => sum + (i.badge ?? 0), 0) : 0
)

// 团队名那一行：和项目侧栏原来那一行同一套规矩（TopicSidebar 的 owningTeam）——个人
// 项目只对本人写「个人」，被邀请进来的人看不到这一行。
function teamOf(projectId: string | undefined): { label: string; handle: string } | null {
  const p = nav?.projects.value.find((x) => x.id === projectId)
  const handle = p?.team_handle
  if (!p || !handle) return null
  if (handle === p.owner_handle) return handle === myHandle() ? { label: t('work.sidebar.personalTeam'), handle } : null
  return { label: p.team_name || handle, handle }
}
const team = computed(() => teamOf(current.value?.projectId))

const open = ref(false)
const query = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
watch(open, async (isOpen) => {
  if (!isOpen) {
    query.value = ''
    return
  }
  await nextTick()
  searchInput.value?.focus()
})

const rows = computed(() => {
  const q = query.value.trim().toLowerCase()
  return projectItems.value
    .filter(
      (i) => !q || i.title.toLowerCase().includes(q) || (teamOf(i.projectId)?.label ?? '').toLowerCase().includes(q)
    )
    .map((item) => ({ item, current: item === current.value, team: teamOf(item.projectId)?.label ?? '' }))
})

const inApp = inDesktopApp()
const keyLabel = (n: number) => railShortcut(n, inApp).keys.join(' ')

function openFirst() {
  const first = rows.value[0]?.item
  if (!first?.to) return
  open.value = false
  void navigation?.navigate(first.to)
}

function createProject() {
  open.value = false
  addItem.value?.action?.()
}

// 拖动排序：和原来方块栏同一个拖拽类型，从外面拖进来的文件、文字不响应。
const DRAG_TYPE = 'application/x-cheese-project'
const draggingId = ref<string | null>(null)
const dropAt = ref<{ id: string; edge: DropEdge } | null>(null)

function edgeFor(id: string | undefined): DropEdge | null {
  return id && dropAt.value?.id === id ? dropAt.value.edge : null
}
function onDragStart(e: DragEvent, id: string) {
  if (!e.dataTransfer || query.value) return
  e.dataTransfer.setData(DRAG_TYPE, id)
  e.dataTransfer.effectAllowed = 'move'
  draggingId.value = id
}
function onDragOver(e: DragEvent, id: string) {
  if (!e.dataTransfer?.types.includes(DRAG_TYPE)) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  dropAt.value =
    id === draggingId.value ? null : { id, edge: e.clientY < box.top + box.height / 2 ? 'before' : 'after' }
}
function onDrop(e: DragEvent, id: string) {
  const moved = e.dataTransfer?.getData(DRAG_TYPE)
  const edge = dropAt.value?.edge
  clearDrag()
  if (!moved || !edge || moved === id) return
  e.preventDefault()
  nav?.reorder(moved, id, edge)
}
function clearDrag() {
  draggingId.value = null
  dropAt.value = null
}
</script>

<style scoped>
.frame-top {
  /* 高度和底边线来自全局 .sidebar-header（48px 基线），和右边内容区顶栏那条线接成一条。 */
  flex: none;
  justify-content: flex-start;
  gap: 6px;
  padding: 6px 8px;
}

/* 老鼠图标沿用首页那一格（RailItem），在侧栏头里缩到 36px。 */
.frame-top__home:deep(.app-rail-item),
.frame-top :deep(.app-rail-item) {
  width: 36px !important;
  height: 36px !important;
  flex: none;
}

.frame-top :deep(.app-rail-item-cheese .cheese-icon) {
  width: 20px !important;
  height: 20px !important;
}

/* 选中竖条贴的是屏幕边，侧栏头里没有那条边，不画。 */
.frame-top :deep(.app-rail-item::before) {
  display: none;
}

.frame-top__switch {
  display: flex;
  flex: 1;
  gap: 6px;
  align-items: center;
  min-width: 0;
  padding: 4px 6px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.frame-top__switch:hover,
.frame-top__switch--open {
  background: var(--fill);
}

.frame-top__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.frame-top__name {
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.frame-top__team {
  overflow: hidden;
  font-size: 12px;
  line-height: 1.3;
  color: var(--muted);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.frame-top__team:hover {
  color: var(--text);
  text-decoration: underline;
}

.frame-top__caret {
  flex: none;
  color: var(--faint);
}

.switcher {
  display: flex;
  flex-direction: column;
  width: 340px;
  max-height: min(560px, 80vh);
  padding: 6px;
  background: var(--raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.switcher__search {
  display: flex;
  gap: 8px;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  margin-bottom: 4px;
  color: var(--faint);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.switcher__search input {
  flex: 1;
  min-width: 0;
  font: inherit;
  font-size: 13px;
  color: var(--ink);
  background: none;
  border: 0;
  outline: 0;
}

.switcher__list {
  overflow-y: auto;
}

.switcher__row-wrap {
  position: relative;
  display: flex;
  align-items: center;
  border-radius: var(--radius-md);
}

.switcher__row-wrap:hover {
  background: var(--fill);
}

.switcher__row-wrap--dragging {
  opacity: 0.4;
}

.switcher__row-wrap--before {
  box-shadow: inset 0 2px 0 var(--muted);
}

.switcher__row-wrap--after {
  box-shadow: inset 0 -2px 0 var(--muted);
}

.switcher__row {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: center;
  min-width: 0;
  height: 32px;
  padding: 0 8px;
  font: inherit;
  font-size: 13.5px;
  color: var(--text);
  text-decoration: none;
  cursor: pointer;
  background: none;
  text-align: left;
  border: 0;
  border-radius: var(--radius-md);
}

button.switcher__row:hover,
a.switcher__row:not(.switcher__row-wrap a):hover {
  background: var(--fill);
}

.switcher__row[aria-current] {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.switcher__grip {
  flex: none;
  margin-left: -4px;
  color: var(--faint);
  cursor: grab;
}

.switcher__home {
  display: grid;
  flex: none;
  place-items: center;
  width: 20px;
  height: 20px;
  background: var(--fill-2);
  border-radius: 5px;
}

.switcher__home svg {
  fill: var(--muted);
}

.switcher__icon {
  color: var(--faint);
}

.switcher__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher__aside {
  flex: none;
  max-width: 6em;
  overflow: hidden;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher__kbd {
  flex: none;
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 16px;
  color: var(--muted);
  border: 1px solid var(--line-2);
  border-radius: 4px;
}

.switcher__more {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  color: var(--faint);
  cursor: pointer;
  visibility: hidden;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.switcher__row-wrap:hover .switcher__more,
.switcher__more:focus-visible,
.switcher__more[aria-expanded='true'] {
  visibility: visible;
}

.switcher__more:hover {
  color: var(--text);
  background: var(--line-2);
}

.switcher__rule {
  flex: none;
  height: 1px;
  margin: 4px;
  background: var(--line);
}

.switcher__empty {
  padding: 10px;
  font-size: 13px;
  color: var(--muted);
}
</style>
