<template>
  <MineView :teams="myTeams" :loading="isLoadingMyTeams" :error="loadMyTeamsError" @reload="fetchMyTeams" />
</template>

<script setup lang="ts">
// 「我加入的小队」这一页的**容器**：取数在 `onMounted` 里发起，画的那一半在 `MineView.vue`。
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
