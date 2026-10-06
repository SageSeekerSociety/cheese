<script setup lang="ts">
// 首页那一格的目录，画出来的那一半：待办、我所在的团队、我加入的空间。
//
// 桌面上它是首页侧栏的正文（HomeSidebar），手机上是底栏「首页」那一格的整页
// （HomeHub）——同一份目录，两端不各写一份。
//
// 团队在原地展开：一个团队有五样东西（项目、成员、知识库、工作电脑、额度），点哪样
// 右边就打开哪样，侧栏不动。个人团队只有你一个人，所以没有「成员」这一样，也没有
// 「邀请成员」。空间不展开：空间自己有一整套目录，点进去就是那个空间。
//
// 名单、展开状态、四个对话框的开合都不在这里：这一只只吃 props，点了什么喊一声
// （`toggle` / `teamAction` / 四个 `submit`），取数和成功之后的事由 `useHomeNav` 那一侧
// 办。
import type { MenuAction } from '@/components/common/menuAction'
import type { Team, TeamMember } from '@/types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { useRowMenu } from '@/composables/useRowMenu'

import JoinSpaceDialog from './JoinSpaceDialog.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import DisbandTeamDialog from '@/views/teams/DisbandTeamDialog.vue'
import TeamProfileEditDialog from '@/views/teams/TeamProfileEditDialog.vue'
import TransferTeamDialog from '@/views/teams/TransferTeamDialog.vue'

const props = defineProps<{
  /** 手机上「待办」是底栏的一格，这里就不再列一次。 */
  inbox?: boolean
  /** 等我处理的件数，画在「待办」那一行右边。 */
  awaiting: number
  /** 我所在的团队：自己名下那个（`personal`）排在前、不带小标题。 */
  teams: Team[]
  /** 我加入的空间。 */
  spaces: { id: number; name: string }[]
  /** 哪些团队是展开的（按 handle 记，比较时不看大小写）。 */
  openHandles: string[]
  /** 用邀请码加入空间。 */
  join: { open: boolean; busy: boolean; error: string | null }
  /** 改小队资料。 */
  profile: { team: Team | null; busy: boolean; error: string; nameError: string }
  /** 解散小队。 */
  disband: { team: Team | null; busy: boolean; error: string | null }
  /** 转让小队：`members` 是能接手的人（团队里除我以外的成员）。 */
  transfer: { team: Team | null; members: TeamMember[]; busy: boolean; error: string | null }
}>()

const emit = defineEmits<{
  /** 展开/收起一个团队。 */
  toggle: [handle: string]
  /** 点了那一行 ⋯ 里的一件：「邀请」是一条链接，不走这里。 */
  teamAction: [action: 'edit' | 'transfer' | 'disband' | 'leave', team: Team]
  /** 某个对话框要关。 */
  dismiss: [dialog: 'join' | 'profile' | 'disband' | 'transfer']
  /** 点了「加入空间」。 */
  openJoin: []
  joinSubmit: [code: string]
  profileSubmit: [draft: { name: string; intro: string; avatarFile?: File }]
  disbandSubmit: []
  transferSubmit: [userId: number]
}>()

// 自己名下（只有自己的那个团队，以自己的昵称出现）一组，真团队一组，后一组才有「团队」小标题。
const groups = computed(() => [
  { key: 'own', heading: false, teams: props.teams.filter((team) => team.personal) },
  { key: 'teams', heading: true, teams: props.teams.filter((team) => !team.personal) },
])

const isOpen = (team: Team) => props.openHandles.some((h) => h.toLowerCase() === team.handle.toLowerCase())

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

/** 一个团队那一行的 ⋯（和右键）里能做的事：管理员邀请、改资料；不是所有者的能退出。 */
function teamActions(team: Team): MenuAction[] {
  if (team.personal) return []
  const actions: MenuAction[] = []
  if (isAdmin(team))
    actions.push(
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
        onSelect: () => emit('teamAction', 'edit', team),
      }
    )
  // 所有者退不掉（后端拒：先转让或解散），所以他看到的是那两条出路。
  if (team.role === 'OWNER')
    actions.push(
      {
        key: 'transfer',
        label: t('home.nav.transferTeam'),
        icon: 'mdi-account-arrow-right-outline',
        onSelect: () => emit('teamAction', 'transfer', team),
      },
      {
        key: 'disband',
        label: t('home.nav.disbandTeam'),
        icon: 'mdi-delete-outline',
        danger: true,
        onSelect: () => emit('teamAction', 'disband', team),
      }
    )
  else
    actions.push({
      key: 'leave',
      label: t('home.nav.leaveTeam'),
      icon: 'mdi-exit-to-app',
      danger: true,
      onSelect: () => emit('teamAction', 'leave', team),
    })
  return actions
}

// 右键一行，弹的就是 ⋯ 那一份，弹在鼠标那一点上。
const rowMenu = useRowMenu<number>()
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
          @click="emit('toggle', team.handle)"
          @contextmenu="teamActions(team).length && rowMenu.open(team.id, $event)"
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
              <!-- No request when avatarId is empty: getAvatarUrl(null) returns /avatars/default,
                 which the backend answers 404 by design when the file is missing, filling the
                 console with an error. -->
              <v-img v-if="team.avatarId" :src="getAvatarUrl(team.avatarId)">
                <template #error>{{ team.name.slice(0, 1) }}</template>
              </v-img>
              <template v-else>{{ team.name.slice(0, 1) }}</template>
            </v-avatar>
          </template>
          <v-list-item-title class="home-nav__name" data-user-content>{{ team.name }}</v-list-item-title>
          <template #append>
            <AdaptiveMenu
              v-if="teamActions(team).length"
              v-bind="rowMenu.bind(team.id)"
              :actions="teamActions(team)"
              :title="team.name"
            >
              <template #activator="{ props: activator }">
                <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
                <v-btn
                  v-bind="activator"
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
      @click="emit('openJoin')"
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

  <JoinSpaceDialog
    :model-value="join.open"
    :joining="join.busy"
    :error="join.error ?? undefined"
    @update:model-value="emit('dismiss', 'join')"
    @submit="emit('joinSubmit', $event)"
  />
  <TeamProfileEditDialog
    v-if="profile.team"
    :model-value="true"
    :team="profile.team"
    :saving="profile.busy"
    :error="profile.error"
    :name-error="profile.nameError"
    @update:model-value="emit('dismiss', 'profile')"
    @save="emit('profileSubmit', $event)"
  />
  <DisbandTeamDialog
    v-if="disband.team"
    :model-value="true"
    :team="disband.team"
    :disbanding="disband.busy"
    :error="disband.error"
    @update:model-value="emit('dismiss', 'disband')"
    @submit="emit('disbandSubmit')"
  />
  <TransferTeamDialog
    v-if="transfer.team"
    :model-value="true"
    :team="transfer.team"
    :candidates="transfer.members"
    :transferring="transfer.busy"
    :error="transfer.error"
    @update:model-value="emit('dismiss', 'transfer')"
    @submit="emit('transferSubmit', $event)"
  />
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
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  background: var(--fill-2);

  /* 形状照 GitHub：人是圆的，团队、空间是圆角方块。 */
  border-radius: var(--radius-md) !important;
  flex: none;
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
  font-size: 14px;
  color: var(--ink);
}

.home-nav__leaf {
  padding-inline-start: 58px !important;
}

.home-nav__leaf :deep(.v-list-item-title) {
  font-size: 13px;
  color: var(--muted);
}

.home-nav__action :deep(.v-list-item-title) {
  font-size: 13px;
  color: var(--muted);
}

.home-nav__meta {
  font-size: 12px;
  color: var(--faint);
}

.home-nav__more {
  margin-inline-start: 4px;
  color: var(--muted);
}

/* 手指点得中（设计系统 §10.1）：这颗只有 24px，触屏上把能点的范围撑到 44×44，画出来
   的样子不变。 */
@media (pointer: coarse) {
  .home-nav__more::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }
}
</style>
