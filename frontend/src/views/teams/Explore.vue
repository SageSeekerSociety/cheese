<template>
  <ExploreView
    v-model:search-query="searchQuery"
    :teams="searchTeamsData"
    :has-searched="hasSearched"
    :failed="searchFailed"
    :reason="searchFailureReason"
    :forbidden="searchForbidden"
    @search="fetchSearchResults"
    @clear="clearSearch"
  />
</template>

<script setup lang="ts">
// 探索小队这一页的**容器**：搜索、取数、清空都在这儿，画的那一半在 `ExploreView.vue`。
//
// 「没读到」和「没有结果」是两件事：前者记在 `searchError` 里，交给视图按失败的样子画，
// 只有读到了、确实数出 0 个，才是「没有结果」。
import type { Team } from '@/types'

import { computed, ref } from 'vue'

import ExploreView from './ExploreView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { TeamsApi } from '@/network/api/teams'

const searchQuery = ref('')
const searchTeamsData = ref<Team[]>([])
const hasSearched = ref(false)
/** 这一次搜索没读到的那个错。非空就说明「没有结果」是假的，是没读到。 */
const searchError = ref<unknown>(null)
const searchFailed = computed(() => searchError.value !== null)
const searchFailureReason = computed(() => loadFailureReason(searchError.value))
const searchForbidden = computed(() => isForbidden(searchError.value))

const fetchSearchResults = async (query: string) => {
  if (!query) {
    clearSearch()
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

const clearSearch = () => {
  searchQuery.value = ''
  searchTeamsData.value = []
  hasSearched.value = false
  searchError.value = null
}
</script>
