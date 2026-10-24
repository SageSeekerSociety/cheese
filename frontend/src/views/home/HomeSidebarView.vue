<script setup lang="ts">
// 首页那一格的侧栏（桌面）—— `HomeSidebar.vue` 的画面这一半：外面那只抽屉、页头上
// 「首页」那一行，和目录本体（`HomeNav.vue`）。
//
// 抽屉（`SecondaryNavigation`）和目录都得跟版式住在一起，所以两样都在这一份里。值从
// props 进、动作原样报回去：这里不知道后端、不知道地址（A 档）。两边的名字和
// `HomeNav.vue` 那一份一一对应，含义见它。
import type { SpaceLink, Team, TeamMember } from '@/types'

import HomeNav from './HomeNav.vue'

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
  joinOpen: boolean
  joinBusy?: boolean
  joinError?: string
  createOpen: boolean
  createBusy?: boolean
  createError?: string
}>()

const emit = defineEmits<{
  openDisband: [team: Team]
  'update:disbanding': [team: Team | null]
  confirmDisband: []
  openTransfer: [team: Team]
  'update:transferring': [team: Team | null]
  confirmTransfer: [userId: number]
  leave: [team: Team]
  'update:joinOpen': [open: boolean]
  join: [code: string]
  'update:createOpen': [open: boolean]
  createSpace: [payload: { name: string; intro: string; avatarFile?: File }]
}>()
</script>

<template>
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="t-title">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <HomeNav
      inbox
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
      :join-open="joinOpen"
      :join-busy="joinBusy"
      :join-error="joinError"
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
      @update:join-open="(open) => emit('update:joinOpen', open)"
      @join="(code) => emit('join', code)"
      @update:create-open="(open) => emit('update:createOpen', open)"
      @create-space="(payload) => emit('createSpace', payload)"
    />
  </SecondaryNavigation>
</template>
