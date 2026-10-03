<script setup lang="ts">
// 首页那一格的目录：待办、我所在的团队、我加入的空间。
//
// 桌面上它是首页侧栏的正文（HomeSidebar），手机上是底栏「首页」那一格的整页
// （HomeHub）——同一份目录，两端不各写一份。
//
// 团队在原地展开：一个团队有五样东西（项目、成员、知识库、工作电脑、额度），点哪样
// 右边就打开哪样，侧栏不动。个人团队只有你一个人，所以没有「成员」这一样，也没有
// 「邀请成员」。空间不展开：空间自己有一整套目录，点进去就是那个空间。
import type { MenuAction } from '@/components/common/menuAction'
import type { Team } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { awaitingCount } from '@/composables/useAwaitingCount'

import JoinSpaceDialog from './JoinSpaceDialog.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'
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
const spaces = ref<{ id: number; name: string }[]>([])
// 自己名下（只有自己的那个团队，以自己的昵称出现）一组，真团队一组，后一组才有「团队」小标题。
const groups = computed(() => [
  { key: 'own', heading: false, teams: teams.value.filter((team) => team.personal) },
  { key: 'teams', heading: true, teams: teams.value.filter((team) => !team.personal) },
])

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
    spaces.value = data.spaces.map((space) => ({ id: space.id, name: space.name }))
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
  { name: 'TeamsDetailCredits', label: 'home.nav.teamCredits', exact: false },
] as const
// 自己名下谁也加不进来（后端拒），「成员」一页就不列；自己的额度在个人设置里，「额度」
// 一页也不列。
const PERSONAL_HIDDEN: readonly string[] = ['TeamsDetailMembers', 'TeamsDetailCredits']
const pagesOf = (team: Team) =>
  team.personal ? TEAM_PAGES.filter((page) => !PERSONAL_HIDDEN.includes(page.name)) : TEAM_PAGES

const isAdmin = (team: Team) => team.role === 'OWNER' || team.role === 'ADMIN'

const editing = ref<Team | null>(null)

/** 管理员在一个团队那一行的 ⋯ 里能做的事。 */
function teamActions(team: Team): MenuAction[] {
  return [
    {
      key: 'invite',
      label: t('home.nav.inviteMembers'),
      icon: 'mdi-account-plus-outline',
      to: { name: 'TeamsDetailMembers', params: { handle: team.handle }, query: { invite: '1' } },
    },
    {
      key: 'edit',
      label: t('work.teamProfile.edit'),
      icon: 'mdi-pencil-outline',
      onSelect: () => (editing.value = team),
    },
  ]
}
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
  <v-list nav :lines="false" class="home-nav side-nav" bg-color="transparent" density="compact">
    <v-list-item
      v-if="inbox"
      rounded="lg"
      prepend-icon="mdi-inbox-outline"
      :to="{ name: 'inbox' }"
      :title="t('navigation.inbox')"
    >
      <template v-if="awaiting" #append>
        <span class="side-nav__count">{{ awaiting > 99 ? '99+' : awaiting }}</span>
      </template>
    </v-list-item>

    <!-- 自己名下的项目在「团队」之上单独一行：底下是只有自己的那个团队，界面上不当团队说。 -->
    <template v-for="group in groups" :key="group.key">
      <v-list-subheader v-if="group.heading">{{ t('navigation.teams') }}</v-list-subheader>
      <template v-for="team in group.teams" :key="team.id">
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
            <v-avatar
              size="22"
              class="home-nav__mark"
              :class="{ 'home-nav__mark--person': team.personal }"
              data-user-content
            >
              <!-- avatarId 为空时不发请求：getAvatarUrl(null) 回的是 /avatars/default，
                 后端在默认头像缺文件时按设计回 404，会把控制台刷出一条错误。 -->
              <v-img v-if="team.avatarId" :src="getAvatarUrl(team.avatarId)">
                <template #error>{{ team.name.slice(0, 1) }}</template>
              </v-img>
              <template v-else>{{ team.name.slice(0, 1) }}</template>
            </v-avatar>
          </template>
          <v-list-item-title class="home-nav__name" data-user-content>{{ team.name }}</v-list-item-title>
          <template #append>
            <AdaptiveMenu v-if="!team.personal && isAdmin(team)" :actions="teamActions(team)" :title="team.name">
              <template #activator="{ props }">
                <!-- eslint-disable-next-line vue/no-restricted-syntax -- 导航栏按钮，外观由本组件的样式精确控制（§3.6 例外） -->
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
            </AdaptiveMenu>
          </template>
        </v-list-item>
        <template v-if="isOpen(team)">
          <v-list-item
            v-for="page in pagesOf(team)"
            :key="page.name"
            rounded="lg"
            class="home-nav__leaf"
            :exact="page.exact"
            :to="{ name: page.name, params: { handle: team.handle } }"
            :title="t(page.label)"
          />
        </template>
      </template>
    </template>
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-plus"
      :to="{ name: 'HomeTeamsExplore' }"
      :title="t('home.nav.newTeam')"
    />

    <v-list-subheader>{{ t('navigation.spaces') }}</v-list-subheader>
    <v-list-item v-for="space in spaces" :key="space.id" rounded="lg" :to="spaceEntryRoute(space)">
      <template #prepend>
        <span class="home-nav__mark home-nav__mark--letter" aria-hidden="true" data-user-content>{{
          space.name.slice(0, 1)
        }}</span>
      </template>
      <v-list-item-title class="home-nav__name" data-user-content>{{ space.name }}</v-list-item-title>
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
/* 行高、悬停、选中、图标大小都是全站那套侧栏行（common.scss 的 .side-nav）。这里只
   管首页这份目录自己多出来的东西：团队行的箭头和头像、展开出来的四样。 */
/* 每一行的前缀占同样宽：团队行是「箭头 + 头像」，其余行把图标或首字放在头像那一格，
   所以所有名字从同一条竖线开始，展开出来的四样东西也和团队名对齐。 */
.home-nav :deep(.v-list-item__prepend) {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  /* 箭头 16 + 6 + 头像 22 + 6：团队行最宽，这一格按它定，别的行只是左边空着。 */
  width: 50px;
}
/* Vuetify 在图标后面的 spacer 留 8px、头像后面留 0，于是图标行的图标比团队头像往左
   错出 8px。统一成 0，前缀里只剩上面那个 6px 的间隔。 */
.home-nav :deep(.v-list-item__prepend > .v-list-item__spacer) {
  width: 0 !important;
}
/* 图标占头像那一格（22px 宽）居中，名字才和团队名、空间名从同一条竖线开始。 */
.home-nav :deep(.v-list-item__prepend > .v-icon) {
  width: 22px;
}
.home-nav__mark {
  flex: none;
  /* 形状照 GitHub：人是圆的，团队、空间是圆角方块。 */
  border-radius: var(--radius-md) !important;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  background: var(--fill-2);
}
/* 头像读不到（没传过、或头像服务不在）时退回首字，和空间那一格同一个样子。 */
/* 自己名下那一行是本人：用人的圆形。 */
.home-nav__mark--person {
  border-radius: var(--radius-pill) !important;
}
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
  padding-inline-start: 58px !important;
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
</style>
