<script setup lang="ts">
// 首页那一格的目录：待办、我所在的团队、我加入的空间，和挨着空间的市场。
//
// 桌面上它是首页侧栏的正文（HomeSidebar），手机上是底栏「首页」那一格的整页
// （HomeHub）——同一份目录，两端不各写一份。
//
// 团队在原地展开：一个团队有五样东西（项目、成员、知识库、工作电脑、额度），点哪样
// 右边就打开哪样，侧栏不动。个人团队只有你一个人，所以没有「成员」这一样，也没有
// 「邀请成员」。空间不展开：空间自己有一整套目录，点进去就是那个空间。
//
// **这一件只吃 props 出事件**（A 档）：名单、待办数、现在是哪个团队、四个弹窗的开合与
// 它们的进行中/出错，都是外面给的；取数、写回、跳地址在 `composables/useTeamNav.ts`。
// 于是它能单独摆在组件预览站里看（`views/demo/catalogHome.ts`），不用起假后端。
//
// 留在这里的是**画面自己的状态**：哪几行摊开、记在这台浏览器上（`OPEN_KEY`），
// 每一行 ⋯ 里的操作清单长什么样。
import type { MenuAction } from '@/components/common/menuAction'
import type { SpaceLink, Team, TeamMember } from '@/types'

import { computed, ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { useRowMenu } from '@/composables/useRowMenu'

import CreateSpaceDialog from './CreateSpaceDialog.vue'
import JoinSpaceDialog from './JoinSpaceDialog.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { storageGetStrings, storageSetJson } from '@/lib/browserStorage'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import DisbandTeamDialog from '@/views/teams/DisbandTeamDialog.vue'
import TransferTeamDialog from '@/views/teams/TransferTeamDialog.vue'

const props = defineProps<{
  /** 手机上「待办」是底栏的一格，这里就不再列一次。 */
  inbox?: boolean
  /** 我所在的团队（含自己名下那一个）。 */
  teams: Team[]
  /** 我加入的空间，加上我建的、还没过审的那几个。 */
  spaces: SpaceLink[]
  /** 待我处理的件数；0 或没给就不画那颗角标。 */
  awaiting?: number
  /** 正在看的是哪个团队（`/teams/<handle>/…`）——那一个必须是展开的。 */
  currentHandle?: string | null
  /** 正在解散的团队；null 就是没在解散。 */
  disbanding: Team | null
  disbandBusy?: boolean
  disbandError?: string | null
  /** 正在转让的团队；null 就是没在转让。 */
  transferring: Team | null
  /** 能接手的人（已经去掉我自己）。 */
  transferCandidates: TeamMember[]
  transferLoading?: boolean
  transferBusy?: boolean
  transferError?: string | null
  joinOpen: boolean
  joinBusy?: boolean
  joinError?: string
  createOpen: boolean
  createBusy?: boolean
  createError?: string
}>()

const emit = defineEmits<{
  /** 点了「解散团队」。 */
  openDisband: [team: Team]
  'update:disbanding': [team: Team | null]
  confirmDisband: []
  /** 点了「转让团队」。 */
  openTransfer: [team: Team]
  'update:transferring': [team: Team | null]
  confirmTransfer: [userId: number]
  /** 点了「退出团队」：确认那一下和退本身都由外面做。 */
  leave: [team: Team]
  'update:joinOpen': [open: boolean]
  /** 「加入」按下去了：邀请码在里面输好了。 */
  join: [code: string]
  'update:createOpen': [open: boolean]
  /** 「提交审核」按下去了：payload 是弹窗里填的那三样。 */
  createSpace: [payload: { name: string; intro: string; avatarFile?: File }]
}>()

// 自己名下（只有自己的那个团队，以自己的昵称出现）一组，真团队一组，后一组才有「团队」小标题。
const groups = computed(() => [
  { key: 'own', heading: false, teams: props.teams.filter((team) => team.personal) },
  { key: 'teams', heading: true, teams: props.teams.filter((team) => !team.personal) },
])

// 哪些团队是展开的：记在这台浏览器上，下次打开还是那样。存不进去也不要紧。
const OPEN_KEY = 'cheesex.homeNav.openTeams'
function readOpen(): string[] {
  return storageGetStrings(OPEN_KEY)
}
const open = ref<string[]>(readOpen())
function persist() {
  // 存不进去就只在这一次有效。
  storageSetJson(OPEN_KEY, open.value)
}
function toggle(handle: string) {
  open.value = open.value.includes(handle) ? open.value.filter((h) => h !== handle) : [...open.value, handle]
  persist()
}

// 正在看某个团队的某一页时，那个团队一定是展开的：否则侧栏上找不到「你在这儿」。
watch(
  () => props.currentHandle,
  (handle) => {
    if (handle && !open.value.some((h) => h.toLowerCase() === handle.toLowerCase())) {
      open.value = [...open.value, handle]
      persist()
    }
  },
  { immediate: true }
)
const isOpen = (team: Team) => open.value.some((h) => h.toLowerCase() === team.handle.toLowerCase())

// 侧栏上只留常去的两样：项目，和资料库（文档和文件都在里面，同项目的资料库）。成员、
// 设备、模型、额度这些偶尔才去的收在 ⋯ 里的「设置」，盖在窗口上的一层（`teams/TeamSettings.vue`）。
const TEAM_PAGES = [
  { name: 'TeamsDetailDefault', label: 'home.nav.teamProjects', exact: true },
  { name: 'TeamsDetailLibrary', label: 'home.nav.teamLibrary', exact: false },
] as const
// 自己名下的设备、模型和额度在「个人设置」里，那一行的「设置」打开它。
const settingsOf = (team: Team) =>
  team.personal ? { name: 'UserSettingsCompute' } : { name: 'TeamsDetailSettings', params: { handle: team.handle } }

const isAdmin = (team: Team) => team.role === 'OWNER' || team.role === 'ADMIN'

/** 一个团队那一行的 ⋯（和右键）里能做的事：管理员邀请、改资料（打开设置的「基本信息」）；
 *  不是所有者的能退出。 */
function teamActions(team: Team): MenuAction[] {
  const settings: MenuAction = {
    key: 'settings',
    label: t('home.nav.teamSettings'),
    icon: 'mdi-cog-outline',
    to: settingsOf(team),
  }
  if (team.personal) return [settings]
  const actions: MenuAction[] = [settings]
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
        to: { name: 'TeamsDetailProfile', params: { handle: team.handle } },
      }
    )
  // 所有者退不掉（后端拒：先转让或解散），所以他看到的是那两条出路。
  if (team.role === 'OWNER')
    actions.push(
      {
        key: 'transfer',
        label: t('home.nav.transferTeam'),
        icon: 'mdi-account-arrow-right-outline',
        onSelect: () => emit('openTransfer', team),
      },
      {
        key: 'disband',
        label: t('home.nav.disbandTeam'),
        icon: 'mdi-delete-outline',
        danger: true,
        onSelect: () => emit('openDisband', team),
      }
    )
  else
    actions.push({
      key: 'leave',
      label: t('home.nav.leaveTeam'),
      icon: 'mdi-exit-to-app',
      danger: true,
      onSelect: () => emit('leave', team),
    })
  return actions
}

// 右键一行，弹的就是 ⋯ 那一份，弹在鼠标那一点上。
const rowMenu = useRowMenu<number>()

// 弹窗自己那一格开合：外面给的是「在编辑哪个团队」，这里翻成 `v-model` 要的布尔。
// 关掉（点了取消、按了 Esc）就是把那一格交回空。
const joinModel = computed({ get: () => props.joinOpen, set: (value: boolean) => emit('update:joinOpen', value) })
const createModel = computed({
  get: () => props.createOpen,
  set: (value: boolean) => emit('update:createOpen', value),
})

/** 过审的空间点进去是它自己的目录；还在审核或被驳回的，点进去是这份申请。 */
function spaceTo(space: SpaceLink) {
  return space.review ? { name: 'HomeSpaceApplication', params: { spaceId: String(space.id) } } : spaceEntryRoute(space)
}
const disbandModel = computed({
  get: () => props.disbanding !== null,
  set: (value: boolean) => {
    if (!value) emit('update:disbanding', null)
  },
})
const transferModel = computed({
  get: () => props.transferring !== null,
  set: (value: boolean) => {
    if (!value) emit('update:transferring', null)
  },
})
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
          @contextmenu="teamActions(team).length && rowMenu.open(team.id, $event)"
        >
          <template #prepend>
            <v-icon size="16" class="home-nav__caret">{{
              isOpen(team) ? 'mdi-chevron-down' : 'mdi-chevron-right'
            }}</v-icon>
            <!-- 团队头像走 UserAvatar：挑过就画那张，没挑过 / 取不到就画彩色首字母，失败
                 记忆也归它一处管。形状照 GitHub——本人那一行是圆的（person），团队是圆角
                 方块（org），由 UserAvatar 的 kind 决定，这里不再自己写 border-radius。
                 getAvatarUrl(null) 回空串（不是 /avatars/default），所以没图的团队不会去
                 打一个注定 404 的请求。 -->
            <UserAvatar
              size="22"
              class="home-nav__mark"
              :avatar="getAvatarUrl(team.avatarId)"
              :name="team.name"
              :seed="team.handle"
              :kind="team.personal ? 'person' : 'org'"
            />
          </template>
          <v-list-item-title class="home-nav__name" data-user-content>{{ team.name }}</v-list-item-title>
          <template #append>
            <AdaptiveMenu
              v-if="teamActions(team).length"
              v-bind="rowMenu.bind(team.id)"
              :actions="teamActions(team)"
              :title="team.name"
            >
              <template #activator="{ props }">
                <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
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
    </template>
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-plus"
      :to="{ name: 'HomeTeamsExplore' }"
      :title="t('home.nav.newTeam')"
    />

    <v-list-subheader>{{ t('navigation.spaces') }}</v-list-subheader>
    <v-list-item v-for="space in spaces" :key="space.id" rounded="lg" :to="spaceTo(space)">
      <template #prepend>
        <!-- 空间头像也走 UserAvatar（org = 圆角方块）：挑过就画上传的那张，没挑过 /
             取不到就画按 id 派生的彩色首字母，和团队、人的那几格同一套形状规则。
             getAvatarUrl(null) 回空串，所以没图的空间不会去打一个注定 404 的请求。 -->
        <UserAvatar
          size="22"
          class="home-nav__mark"
          :avatar="getAvatarUrl(space.avatarId)"
          :name="space.name"
          :seed="String(space.id)"
          kind="org"
        />
      </template>
      <v-list-item-title class="home-nav__name" data-user-content>{{ space.name }}</v-list-item-title>
      <template #append>
        <span v-if="space.review" class="home-nav__review" :class="`home-nav__review--${space.review}`">{{
          t(`home.nav.spaceReview.${space.review}`)
        }}</span>
        <v-icon v-else size="16" class="home-nav__meta">mdi-chevron-right</v-icon>
      </template>
    </v-list-item>
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-ticket-confirmation-outline"
      :title="t('work.joinAction')"
      @click="emit('update:joinOpen', true)"
    />
    <v-list-item
      rounded="lg"
      class="home-nav__action"
      prepend-icon="mdi-plus"
      :title="t('spaces.create.open')"
      @click="emit('update:createOpen', true)"
    />

    <!-- 市场：技能、插件、MCP、Agent 模板，全站一份、不属于哪个团队或空间。入口只在这份目录
         里（桌面侧栏和手机「首页」整页都是它），挨着空间那一段，不占 rail 的格子。 -->
    <v-divider class="my-2" />
    <v-list-item
      rounded="lg"
      prepend-icon="mdi-storefront-outline"
      :to="{ name: 'market' }"
      :title="t('navigation.market')"
    />
  </v-list>

  <JoinSpaceDialog v-model="joinModel" :busy="joinBusy" :error="joinError" @submit="(code) => emit('join', code)" />
  <CreateSpaceDialog
    v-model="createModel"
    :busy="createBusy"
    :error="createError"
    @submit="(payload) => emit('createSpace', payload)"
  />
  <DisbandTeamDialog
    v-if="disbanding"
    v-model="disbandModel"
    :team="disbanding"
    :busy="disbandBusy"
    :error="disbandError"
    @confirm="emit('confirmDisband')"
  />
  <TransferTeamDialog
    v-if="transferring"
    v-model="transferModel"
    :team="transferring"
    :candidates="transferCandidates"
    :loading="transferLoading"
    :busy="transferBusy"
    :error="transferError"
    @confirm="(userId) => emit('confirmTransfer', userId)"
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

/* 头像（团队 / 空间那几格）现在整支交给 UserAvatar：底色、首字母字色、圆形 / 圆角方块，
   都在它里面一处定，这里只留布局上的 `flex: none`。以前这里自己写 border-radius、
   font-size、background 复刻一份，还带一个 `--person` 圆/方的分支，和 UserAvatar 的
   规则一旦漂移就会两处不一致。 */
.home-nav__mark {
  flex: none;
}

.home-nav__more {
  width: 24px;
  height: 24px;
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

/* 还没过审的空间那一行：右边一枚小标签说是哪一种。待审核是在等人，用警示那一组；
   被驳回要他自己动手，用危险那一组的字色，底是中性的。 */
.home-nav__review {
  font-size: 12px;
  line-height: var(--lh-12);
  padding: 0 6px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.home-nav__review--PENDING {
  background: var(--warn-wash);
  color: var(--warn-ink);
}

.home-nav__review--REJECTED {
  background: var(--fill-2);
  color: var(--danger-ink);
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
