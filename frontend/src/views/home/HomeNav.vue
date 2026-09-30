<script setup lang="ts">
// 首页那一格的目录：待办、我所在的团队、我加入的空间。
//
// 桌面上它是首页侧栏的正文（HomeSidebar），手机上是底栏「首页」那一格的整页
// （HomeHub）——同一份目录，两端不各写一份。
//
// 团队在原地展开：一个团队只有四样东西（项目、成员、知识库、工作电脑），点哪样
// 右边就打开哪样，侧栏不动。空间不展开：空间自己有一整套目录，点进去就是那个空间。
import type { Team } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { awaitingCount } from '@/composables/useAwaitingCount'

import JoinSpaceDialog from './JoinSpaceDialog.vue'

import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/courseNav'
import { SpacesApi } from '@/network/api/spaces'
import { TeamsApi } from '@/network/api/teams'
import TeamProfileEditDialog from '@/views/teams/TeamProfileEditDialog.vue'

defineProps<{
  /** 手机上「待办」是底栏的一格，这里就不再列一次。 */
  inbox?: boolean
}>()

const route = useRoute()
const awaiting = awaitingCount()

const teams = ref<Team[]>([])
const spaces = ref<{ id: number; name: string; isCourse?: boolean }[]>([])

async function loadTeams() {
  try {
    teams.value = (await TeamsApi.getMyTeams()).data.teams
  } catch {
    // 读不到就不列：这是一份目录，不是这一页的内容。
  }
}

async function loadSpaces() {
  try {
    const { data } = await SpacesApi.list({ pageSize: 50, sort_by: 'created_at', sort_order: 'desc' })
    spaces.value = data.spaces.map((space) => ({ id: space.id, name: space.name, isCourse: space.isCourse }))
  } catch {
    // 同上。
  }
}

onMounted(() => {
  void loadTeams()
  void loadSpaces()
})

// 哪些团队是展开的：记在这台浏览器上，下次打开还是那样。存不进去也不要紧。
const OPEN_KEY = 'cheesex.homeNav.openTeams'
function readOpen(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')
    return Array.isArray(saved) ? saved.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}
const open = ref<string[]>(readOpen())
function persist() {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify(open.value))
  } catch {
    // 存不进去就只在这一次有效。
  }
}
function toggle(handle: string) {
  open.value = open.value.includes(handle) ? open.value.filter((h) => h !== handle) : [...open.value, handle]
  persist()
}

// 正在看某个团队的某一页时，那个团队一定是展开的：否则侧栏上找不到「你在这儿」。
const currentHandle = computed(() =>
  typeof route.params.handle === 'string' && route.path.startsWith('/teams/') ? route.params.handle : null
)
watch(
  currentHandle,
  (handle) => {
    if (handle && !open.value.some((h) => h.toLowerCase() === handle.toLowerCase())) {
      open.value = [...open.value, handle]
      persist()
    }
  },
  { immediate: true }
)
const isOpen = (team: Team) => open.value.some((h) => h.toLowerCase() === team.handle.toLowerCase())

const TEAM_PAGES = [
  { name: 'TeamsDetailDefault', label: 'home.nav.teamProjects', exact: true },
  { name: 'TeamsDetailMembers', label: 'home.nav.teamMembers', exact: false },
  { name: 'TeamsDetailKnowledge', label: 'home.nav.teamKnowledge', exact: false },
  { name: 'TeamsDetailCompute', label: 'home.nav.teamCompute', exact: false },
] as const

const isAdmin = (team: Team) => team.role === 'OWNER' || team.role === 'ADMIN'

const editing = ref<Team | null>(null)
const editOpen = computed({
  get: () => editing.value !== null,
  set: (value: boolean) => {
    if (!value) editing.value = null
  },
})
function onTeamUpdated(updated: Team) {
  teams.value = teams.value.map((team) => (team.id === updated.id ? { ...team, ...updated } : team))
}

const joinOpen = ref(false)
</script>

<template>
  <v-list nav :lines="false" class="home-nav" bg-color="transparent" density="compact">
    <v-list-item
      v-if="inbox"
      rounded="lg"
      prepend-icon="mdi-inbox-outline"
      :to="{ name: 'inbox' }"
      :title="t('navigation.inbox')"
    >
      <template v-if="awaiting" #append>
        <span class="home-nav__count">{{ awaiting > 99 ? '99+' : awaiting }}</span>
      </template>
    </v-list-item>

    <v-list-subheader class="home-nav__section">{{ t('navigation.teams') }}</v-list-subheader>
    <template v-for="team in teams" :key="team.id">
      <v-list-item
        rounded="lg"
        class="home-nav__team"
        :aria-expanded="isOpen(team)"
        :aria-label="t(isOpen(team) ? 'home.nav.collapse' : 'home.nav.expand', { name: team.name })"
        @click="toggle(team.handle)"
      >
        <template #prepend>
          <v-icon size="16" class="home-nav__caret">{{
            isOpen(team) ? 'mdi-chevron-down' : 'mdi-chevron-right'
          }}</v-icon>
          <v-avatar size="22" rounded="md" class="home-nav__mark">
            <v-img :src="getAvatarUrl(team.avatarId)">
              <template #error>{{ team.name.slice(0, 1) }}</template>
            </v-img>
          </v-avatar>
        </template>
        <v-list-item-title class="home-nav__name">{{ team.name }}</v-list-item-title>
        <template #append>
          <v-menu v-if="isAdmin(team)" location="bottom end">
            <template #activator="{ props }">
              <v-btn
                v-bind="props"
                icon="mdi-dots-horizontal"
                size="x-small"
                variant="text"
                class="home-nav__more"
                :aria-label="t('home.nav.teamActions')"
                @click.stop
              />
            </template>
            <v-list density="compact">
              <v-list-item
                :to="{ name: 'TeamsDetailMembers', params: { handle: team.handle }, query: { invite: '1' } }"
                :title="t('home.nav.inviteMembers')"
              />
              <v-list-item :title="t('work.teamProfile.edit')" @click="editing = team" />
            </v-list>
          </v-menu>
        </template>
      </v-list-item>
      <template v-if="isOpen(team)">
        <v-list-item
          v-for="page in TEAM_PAGES"
          :key="page.name"
          rounded="lg"
          class="home-nav__leaf"
          :exact="page.exact"
          :to="{ name: page.name, params: { handle: team.handle } }"
          :title="t(page.label)"
        />
      </template>
    </template>
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-plus"
      :to="{ name: 'HomeTeamsExplore' }"
      :title="t('home.nav.newTeam')"
    />

    <v-list-subheader class="home-nav__section">{{ t('navigation.spaces') }}</v-list-subheader>
    <v-list-item v-for="space in spaces" :key="space.id" rounded="lg" :to="spaceEntryRoute(space)">
      <template #prepend>
        <span class="home-nav__mark home-nav__mark--letter" aria-hidden="true">{{ space.name.slice(0, 1) }}</span>
      </template>
      <v-list-item-title class="home-nav__name">{{ space.name }}</v-list-item-title>
      <template #append>
        <v-icon size="16" class="home-nav__meta">mdi-chevron-right</v-icon>
      </template>
    </v-list-item>
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-ticket-confirmation-outline"
      :title="t('work.joinAction')"
      @click="joinOpen = true"
    />
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-view-grid-outline"
      :to="{ name: 'HomeSpaces' }"
      exact
      :title="t('work.allSpaces')"
    />
  </v-list>

  <JoinSpaceDialog v-model="joinOpen" @joined="loadSpaces" />
  <TeamProfileEditDialog v-if="editing" v-model="editOpen" :team="editing" @updated="onTeamUpdated" />
</template>

<style scoped>
/* 选中与悬停都是中性色，同 TopicSidebar 的 .nav-row：这条侧栏坐在 --canvas 上，
   --fill 在那上面几乎看不见，所以悬停取 --fill-2、选中取 --line-2。琥珀在导航里
   只留给左栏那一格「当前在哪」和未读。 */
.home-nav .v-list-item {
  min-height: 34px;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.home-nav .v-list-item:hover {
  background: var(--fill-2);
}
.home-nav .v-list-item--active,
.home-nav .v-list-item--active:hover {
  background: var(--line-2);
}
.home-nav .v-list-item--active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.home-nav .v-list-item--active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
.home-nav__section {
  margin-top: 8px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
}
/* 每一行的前缀占同样宽：团队行是「箭头 + 头像」，其余行把图标或首字放在头像那一格，
   所以所有名字从同一条竖线开始，展开出来的四样东西也和团队名对齐。 */
.home-nav :deep(.v-list-item__prepend) {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  width: 44px;
}
.home-nav :deep(.v-list-item__spacer) {
  width: 10px;
}
.home-nav :deep(.v-list-item__prepend > .v-icon) {
  width: 22px;
  margin: 0;
  font-size: 18px;
  color: var(--muted);
  opacity: 1;
}
.home-nav__mark {
  flex: none;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  background: var(--fill-2);
}
/* 头像读不到（没传过、或头像服务不在）时退回首字，和空间那一格同一个样子。 */
.home-nav__mark :deep(.v-img__error) {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
.home-nav__more {
  width: 24px;
  height: 24px;
}
.home-nav__mark--letter {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-md);
}
.home-nav__caret {
  color: var(--faint);
}
.home-nav__name {
  color: var(--ink);
  font-size: 14px;
}
.home-nav__leaf {
  padding-inline-start: 62px !important;
}
.home-nav__leaf :deep(.v-list-item-title) {
  color: var(--muted);
  font-size: 13px;
}
.home-nav__action :deep(.v-list-item-title) {
  color: var(--muted);
  font-size: 13px;
}
.home-nav__meta {
  color: var(--faint);
  font-size: 12px;
}
.home-nav__more {
  margin-inline-start: 4px;
  color: var(--muted);
}
/* 待办的件数：和左栏那一格同一颗角标，属于「未读」那一族，所以是琥珀。 */
.home-nav__count {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--warn);
  color: var(--inverse-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}
</style>
