<template>
  <!-- The place you are in, in the top bar after 首页: a project, a space or the admin area. Clicking it lists
       the other places to go (projects first, then spaces). Outside a place there is nothing
       here: 首页 is the way to everything else. -->
  <span v-if="place" class="place-sep" aria-hidden="true">/</span>
  <v-menu v-if="place" v-model="open" location="bottom start" :offset="6" :close-on-content-click="false">
    <template #activator="{ props: menuProps }">
      <button
        v-bind="menuProps"
        type="button"
        class="place"
        :class="{ 'place--open': open }"
        data-testid="project-switcher"
        :title="t('navigation.switcher.label')"
        :aria-label="t('global.labelWithAside', { label: place.title, aside: t('navigation.switcher.label') })"
        aria-haspopup="menu"
      >
        <ProjectAvatar
          v-if="place.kind === 'project'"
          :id="place.id"
          :name="place.title"
          :avatar="place.avatar"
          :size="20"
        />
        <UserAvatar
          v-else-if="place.kind === 'space'"
          kind="org"
          size="20"
          :avatar="place.avatar"
          :name="place.title"
          :seed="place.id"
        />
        <v-icon v-else class="place__icon" size="18" icon="mdi-shield-account-outline" />
        <span class="place__name" data-user-content>{{ place.title }}</span>
        <v-icon class="place__caret" size="16" icon="mdi-chevron-down" />
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
        <div v-if="rows.length || !query" class="switcher__group">{{ t('navigation.projects') }}</div>
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
            <ProjectAvatar :id="row.item.projectId ?? ''" :name="row.item.title" :avatar="row.item.avatar" :size="20" />
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
        <template v-if="!query">
          <button v-if="addItem" type="button" class="switcher__row" @click="createProject">
            <v-icon size="16" icon="mdi-plus" class="switcher__icon" />
            <span class="switcher__label">{{ addItem.title }}</span>
          </button>
          <router-link :to="{ name: 'my-archived-projects' }" class="switcher__row" @click="open = false">
            <v-icon size="16" icon="mdi-archive-outline" class="switcher__icon" />
            <span class="switcher__label">{{ t('navigation.userMenu.archivedProjects') }}</span>
          </router-link>
        </template>
        <template v-if="spaceRows.length">
          <div class="switcher__rule" />
          <div class="switcher__group">{{ t('navigation.spaces') }}</div>
          <router-link
            v-for="space in spaceRows"
            :key="space.id"
            :to="space.to"
            class="switcher__row"
            :aria-current="space.current ? 'page' : undefined"
            data-testid="space-switcher-row"
            @click="open = false"
          >
            <UserAvatar kind="org" size="20" :avatar="space.avatar" :name="space.title" :seed="String(space.id)" />
            <span class="switcher__label" :title="space.title" data-user-content>{{ space.title }}</span>
          </router-link>
        </template>
        <div v-if="query && !rows.length && !spaceRows.length" class="switcher__empty">
          {{ t('navigation.switcher.empty', { q: query }) }}
        </div>
      </div>
    </div>
  </v-menu>
</template>

<script setup lang="ts">
import type { DropEdge } from '@/lib/projectOrder'
import type { ProjectAvatarSpec } from '@/utils/projectAvatar'

import { computed, nextTick, ref, watch } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { addItemOf, projectItemsOf, useAppNav } from './appNav'
import { railShortcut } from './destinations'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import ProjectAvatar from '@/components/common/ProjectAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import { t } from '@/i18n'
import { inDesktopApp } from '@/lib/desktopApp'
import { myHandle } from '@/me'

const nav = useAppNav()
const navigation = useNavigation()

const items = computed(() => nav?.items.value ?? [])
const addItem = computed(() => addItemOf(items.value))
const projectItems = computed(() => projectItemsOf(items.value))

const path = computed(() => navigation?.route?.path ?? '')
const current = computed(() => projectItems.value.find((i) => i.match?.(path.value)) ?? null)
const currentSpace = computed(() => nav?.currentSpace.value ?? null)

type Place =
  | { kind: 'project'; id: string; title: string; avatar: ProjectAvatarSpec | null | undefined }
  | { kind: 'space'; id: string; title: string; avatar: string }
  | { kind: 'area'; id: string; title: string }
const place = computed<Place | null>(() => {
  const project = current.value
  if (project) return { kind: 'project', id: project.projectId ?? '', title: project.title, avatar: project.avatar }
  const space = currentSpace.value
  if (space) return { kind: 'space', id: String(space.id), title: space.title, avatar: space.avatar }
  const area = nav?.currentArea.value
  if (area) return { kind: 'area', id: 'area', title: area }
  return null
})

// 团队名写在每一行右边：个人项目只对本人写「个人」，被邀请进来的人看不到。
function teamOf(projectId: string | undefined): string {
  const p = nav?.projects.value.find((x) => x.id === projectId)
  const handle = p?.team_handle
  if (!p || !handle) return ''
  if (handle === p.owner_handle) return handle === myHandle() ? t('work.sidebar.personalTeam') : ''
  return p.team_name || handle
}

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

const needle = computed(() => query.value.trim().toLowerCase())
const rows = computed(() =>
  projectItems.value
    .map((item) => ({ item, current: item === current.value, team: teamOf(item.projectId) }))
    .filter(
      ({ item, team }) =>
        !needle.value || item.title.toLowerCase().includes(needle.value) || team.toLowerCase().includes(needle.value)
    )
)
const spaceRows = computed(() =>
  (nav?.spaces.value ?? [])
    .filter((space) => !needle.value || space.title.toLowerCase().includes(needle.value))
    .map((space) => ({ ...space, current: space.id === currentSpace.value?.id }))
)

const inApp = inDesktopApp()
const keyLabel = (n: number) => railShortcut(n, inApp).keys.join(' ')

function openFirst() {
  const first = rows.value[0]?.item.to ?? spaceRows.value[0]?.to
  if (!first) return
  open.value = false
  void navigation?.navigate(first)
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
/* 首页和去处之间那条斜线：只是分隔，读屏不念。 */
.place-sep {
  flex: none;
  margin: 0 2px;
  color: var(--faint);
}

/* 顶栏上去处那一格：和顶栏里别的按钮一样高 28、无边、悬停出底色。 */
.place {
  display: inline-flex;
  flex: 0 1 auto;
  gap: 6px;
  align-items: center;
  min-width: 0;
  height: 28px;
  padding: 0 6px 0 8px;
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.place:hover,
.place--open {
  background: var(--fill-2);
}

.place__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__icon {
  flex: none;
  color: var(--muted);
}

.place__caret {
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

.switcher__group {
  padding: 6px 8px 2px;
  font-size: 13px;
  color: var(--muted);
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
