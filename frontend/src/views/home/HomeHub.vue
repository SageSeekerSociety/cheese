<script setup lang="ts">
// 手机底栏「首页」那一格：团队和空间的目录（`HomeNavView`），和桌面首页侧栏是同一份。
// 桌面上这份目录常驻在侧栏里，这一页就没有必要存在，直接去待办。
//
// 这一页只做接线：名单、展开状态、四个对话框都在 `useHomeNav` 里。
import { watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import HomeHubView from './HomeHubView.vue'
import HomeNavView from './HomeNavView.vue'
import { useHomeNav } from './useHomeNav'

const { mdAndUp } = useDisplay()
const router = useRouter()

watchEffect(() => {
  if (mdAndUp.value) void router.replace({ name: 'inbox' })
})

const {
  awaiting,
  teams,
  spaces,
  openHandles,
  join,
  profile,
  disband,
  transfer,
  toggle,
  onTeamAction,
  dismiss,
  openJoin,
  submitJoin,
  submitProfile,
  submitDisband,
  submitTransfer,
} = useHomeNav()
</script>

<template>
  <HomeHubView>
    <HomeNavView
      :awaiting="awaiting"
      :teams="teams"
      :spaces="spaces"
      :open-handles="openHandles"
      :join="join"
      :profile="profile"
      :disband="disband"
      :transfer="transfer"
      @toggle="toggle"
      @team-action="onTeamAction"
      @dismiss="dismiss"
      @open-join="openJoin"
      @join-submit="submitJoin"
      @profile-submit="submitProfile"
      @disband-submit="submitDisband"
      @transfer-submit="submitTransfer"
    />
  </HomeHubView>
</template>
