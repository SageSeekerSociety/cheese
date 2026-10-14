<script setup lang="ts">
// 手机底栏「首页」那一格 —— 接线这一半。取数、写回后端、盯路由、开哪个弹窗，全在
// `useTeamNav` 里（见那一份）；画面在 `HomeHubView.vue`。
//
// 桌面上这份目录常驻在侧栏里，这一页就没有必要存在，直接去待办。
import { watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useTeamNav } from '@/composables/useTeamNav'

import HomeHubView from './HomeHubView.vue'

const { mdAndUp } = useDisplay()
const router = useRouter()

watchEffect(() => {
  if (mdAndUp.value) void router.replace({ name: 'inbox' })
})

const {
  teams,
  spaces,
  awaiting,
  currentHandle,
  joinOpen,
  joinBusy,
  joinError,
  joinSpace,
  editing,
  editingBusy,
  editingError,
  editingNameError,
  edit,
  clearErrors,
  saveProfile,
  disbanding,
  disbandBusy,
  disbandError,
  openDisband,
  confirmDisband,
  transferring,
  transferCandidates,
  transferLoading,
  transferBusy,
  transferError,
  openTransfer,
  confirmTransfer,
  leaveTeam,
} = useTeamNav()
</script>

<template>
  <HomeHubView
    :teams="teams"
    :spaces="spaces"
    :awaiting="awaiting"
    :current-handle="currentHandle"
    v-model:editing="editing"
    :editing-busy="editingBusy"
    :editing-error="editingError"
    :editing-name-error="editingNameError"
    v-model:disbanding="disbanding"
    :disband-busy="disbandBusy"
    :disband-error="disbandError"
    v-model:transferring="transferring"
    :transfer-candidates="transferCandidates"
    :transfer-loading="transferLoading"
    :transfer-busy="transferBusy"
    :transfer-error="transferError"
    v-model:join-open="joinOpen"
    :join-busy="joinBusy"
    :join-error="joinError"
    @edit="edit"
    @clear-errors="clearErrors"
    @save-profile="saveProfile"
    @open-disband="openDisband"
    @confirm-disband="confirmDisband"
    @open-transfer="openTransfer"
    @confirm-transfer="confirmTransfer"
    @leave="leaveTeam"
    @join="joinSpace"
  />
</template>
