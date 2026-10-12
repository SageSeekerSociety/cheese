<template>
  <MineView
    :is-loading-my-teams="isLoadingMyTeams"
    :load-my-teams-error="loadMyTeamsError"
    :my-teams="myTeams"
    @fetch-my-teams="fetchMyTeams"
  />
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { onMounted, ref } from 'vue'

import MineView from './MineView.vue'

import { TeamsApi } from '@/network/api/teams'

const myTeams = ref<Team[]>([])
const isLoadingMyTeams = ref(false)
const loadMyTeamsError = ref(false)

const fetchMyTeams = async () => {
  isLoadingMyTeams.value = true
  loadMyTeamsError.value = false

  try {
    const {
      data: { teams },
    } = await TeamsApi.getMyTeams()
    // 自己名下的项目不是一个团队，在侧栏「团队」之上单独一行，不列在这里。
    myTeams.value = teams.filter((team) => !team.personal)
  } catch (error) {
    console.error('Failed to load my teams:', error)
    loadMyTeamsError.value = true
  } finally {
    isLoadingMyTeams.value = false
  }
}

onMounted(async () => {
  await fetchMyTeams()
})
</script>
