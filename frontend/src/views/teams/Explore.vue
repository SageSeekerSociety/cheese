<template>
  <ExploreView
    v-model:search-query="searchQuery"
    v-model:search-teams-data="searchTeamsData"
    v-model:has-searched="hasSearched"
    v-model:search-error="searchError"
    :md-and-up="mdAndUp"
    :search-failed="searchFailed"
    :reason="searchFailureReason"
    :forbidden="searchForbidden"
    @fetch-search-results="fetchSearchResults"
  />
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import ExploreView from './ExploreView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { TeamsApi } from '@/network/api/teams'

const { mdAndUp } = useDisplay()
const searchQuery = ref('')
const searchTeamsData = ref<Team[]>([])
const hasSearched = ref(false)
/** 这一次搜索没读到的那个错。非空就说明「没有结果」是假的，是没读到。 */
const searchError = ref<unknown>(null)
const searchFailed = computed(() => searchError.value !== null)
// 「没读到」和「没权限」分开判，再交给那一半画——它不连网络层，也拿不到这个判定。
const searchFailureReason = computed(() => loadFailureReason(searchError.value))
const searchForbidden = computed(() => isForbidden(searchError.value))

const fetchSearchResults = async (query: string) => {
  if (!query) {
    searchTeamsData.value = []
    hasSearched.value = false
    searchError.value = null
    return
  }

  hasSearched.value = true
  // 上一次的失败不许留到这一次：重新问一次，屏幕上先干净。
  searchError.value = null
  try {
    const {
      data: { teams },
    } = await TeamsApi.search({ query })
    searchTeamsData.value = teams
  } catch (error) {
    // 不再弹红条：这一块就在原地换成失败的样子，同一件事不说两遍。
    console.error(error)
    searchError.value = error
  }
}
</script>
