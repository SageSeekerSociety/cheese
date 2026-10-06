<script setup lang="ts">
// 桌面上的首页侧栏：抽屉外壳（`HomeSidebarView`）+ 同一份目录（`HomeNavView`）。
//
// 这一页只做接线：名单、展开状态、四个对话框都在 `useHomeNav` 里。手机上没有常驻侧栏，
// 这份目录是底栏「首页」那一格的整页（HomeHub）。
import HomeNavView from './HomeNavView.vue'
import HomeSidebarView from './HomeSidebarView.vue'
import { useHomeNav } from './useHomeNav'

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
  <HomeSidebarView>
    <HomeNavView
      inbox
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
  </HomeSidebarView>
</template>
