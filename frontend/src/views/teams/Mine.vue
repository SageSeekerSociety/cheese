<template>
  <MineView
    :is-loading-my-teams="isLoadingMyTeams"
    :load-my-teams-error="loadMyTeamsError"
    :my-teams="myTeams"
    @fetch-my-teams="() => fetchMyTeams(true)"
  />
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { onMounted, ref } from 'vue'

import MineView from './MineView.vue'

import { queryClient } from '@/query/client'
import { myTeamsQuery } from '@/query/homeNav'

const myTeams = ref<Team[]>([])
const isLoadingMyTeams = ref(false)
const loadMyTeamsError = ref(false)

const fetchMyTeams = async (fresh = false) => {
  isLoadingMyTeams.value = true
  loadMyTeamsError.value = false

  try {
    const teams = await queryClient.fetchQuery(fresh ? { ...myTeamsQuery(), staleTime: 0 } : myTeamsQuery())
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
