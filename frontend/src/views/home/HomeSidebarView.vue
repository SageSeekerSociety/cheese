<script setup lang="ts">
// 首页那一格的侧栏（桌面）—— `HomeSidebar.vue` 的画面这一半：外面那只抽屉、页头上
// 「首页」那一行，和目录本体（`HomeNav.vue`）。
//
// 抽屉（`SecondaryNavigation`）和目录都得跟版式住在一起，所以两样都在这一份里。值从
// props 进、动作原样报回去：这里不知道后端、不知道地址（A 档）。两边的名字和
// `HomeNav.vue` 那一份一一对应，含义见它。
import type { SpaceLink, Team, TeamMember } from '@/types'

import { computed } from 'vue'

import HomeNav from './HomeNav.vue'

import { addItemOf, projectItemsOf, useAppNav } from '@/components/common/Navigation/appNav'
import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { t } from '@/i18n'

defineProps<{
  /** 视口 ≥960：只有这一档才有常驻侧栏；手机上这一份整个不画。 */
  mdAndUp: boolean
  teams: Team[]
  spaces: SpaceLink[]
  awaiting?: number
  currentHandle?: string | null
  disbanding: Team | null
  disbandBusy?: boolean
  disbandError?: string | null
  transferring: Team | null
  transferCandidates: TeamMember[]
  transferLoading?: boolean
  transferBusy?: boolean
  transferError?: string | null
  createOpen: boolean
  createBusy?: boolean
  createError?: string
}>()

// 桌面上有外壳：侧栏头和搜索由侧栏头画（SidebarFrameTop），这里不再画标题；「待办」那一行
// 照画（侧栏头上原来那一行「首页」2026-11-19 删了）。项目列在团队上面，各带等你处理的
// 件数——和侧栏头切换列表里同一份格子（destinations.ts 算的件数）。
const appNav = useAppNav()
const framed = computed(() => !!appNav)
const projects = computed(() =>
  appNav
    ? projectItemsOf(appNav.items.value).map((item) => ({
        id: item.projectId ?? item.key,
        name: item.title,
        avatar: item.avatar,
        to: item.to ?? '/',
        awaiting: item.badge,
      }))
    : undefined
)
const addProject = computed(() => (appNav ? addItemOf(appNav.items.value) : null))

const emit = defineEmits<{
  openDisband: [team: Team]
  'update:disbanding': [team: Team | null]
  confirmDisband: []
  openTransfer: [team: Team]
  'update:transferring': [team: Team | null]
  confirmTransfer: [userId: number]
  leave: [team: Team]
  'update:createOpen': [open: boolean]
  createSpace: [payload: { name: string; intro: string; avatarFile?: File }]
}>()
</script>

<template>
  <SecondaryNavigation v-if="mdAndUp">
    <!-- Desktop draws the head and the search in the sidebar frame (SidebarFrameTop);
         without a shell (unit tests, the catalog) the title stays here. -->
    <div v-if="!framed" class="sidebar-header">
      <span class="t-title">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <!-- 「待办」这一行始终画：侧栏头上那一行「首页」（也去待办）2026-11-19 删掉了，
         不画它，首页的桌面上就没有一条去 /inbox 的路。 -->
    <HomeNav
      inbox
      :projects="projects"
      :new-project-label="addProject?.title"
      :teams="teams"
      :spaces="spaces"
      :awaiting="awaiting"
      :current-handle="currentHandle"
      :disbanding="disbanding"
      :disband-busy="disbandBusy"
      :disband-error="disbandError"
      :transferring="transferring"
      :transfer-candidates="transferCandidates"
      :transfer-loading="transferLoading"
      :transfer-busy="transferBusy"
      :transfer-error="transferError"
      :create-open="createOpen"
      :create-busy="createBusy"
      :create-error="createError"
      @open-disband="(team) => emit('openDisband', team)"
      @update:disbanding="(team) => emit('update:disbanding', team)"
      @confirm-disband="emit('confirmDisband')"
      @open-transfer="(team) => emit('openTransfer', team)"
      @update:transferring="(team) => emit('update:transferring', team)"
      @confirm-transfer="(userId) => emit('confirmTransfer', userId)"
      @leave="(team) => emit('leave', team)"
      @update:create-open="(open) => emit('update:createOpen', open)"
      @create-space="(payload) => emit('createSpace', payload)"
      @create-project="addProject?.action?.()"
    />
  </SecondaryNavigation>
</template>
