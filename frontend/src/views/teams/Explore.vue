<template>
  <!-- 广告词只给第一次来的人看，而底栏点进来的是每天回来的人：手机上它连
         内边距要吃掉 128px，一屏去掉一大半，下面才是你来这儿要用的东西。 -->
  <div v-if="mdAndUp" class="w-100 header-corner-glow-flow teams-explore-header-container">
    <div class="teams-explore-header h-100 d-flex flex-row align-stretch justify-space-between">
      <div class="px-8 py-16">
        <div class="text-h4 text-high-emphasis">{{ t('teams.explore.heroTitle') }}</div>
        <div class="text-subtitle-1 text-medium-emphasis mt-1">{{ t('teams.explore.heroSubtitle') }}</div>
      </div>
    </div>
  </div>
  <v-container class="teams-container" fluid>
    <v-row>
      <v-col cols="12">
        <v-card flat class="main-card">
          <!-- 搜索框 -->
          <v-card-text class="pb-0">
            <v-form class="mb-4" @submit.prevent="fetchSearchResults(searchQuery)">
              <v-text-field
                v-model="searchQuery"
                autocomplete="off"
                density="comfortable"
                :placeholder="t('teams.explore.searchPlaceholder')"
                prepend-inner-icon="mdi-magnify"
                rounded="lg"
                variant="outlined"
                color="primary"
                hide-details
                class="search-field"
                bg-color="background"
                @keyup.enter="fetchSearchResults(searchQuery)"
              >
                <template #append>
                  <BaseButton
                    v-if="searchQuery"
                    icon="mdi-close"
                    size="sm"
                    :aria-label="t('work.mcp.action.clear')"
                    @click="
                      () => {
                        searchQuery = ''
                        searchTeamsData = []
                        hasSearched = false
                      }
                    "
                  />
                </template>
              </v-text-field>
            </v-form>
          </v-card-text>

          <!-- 动态内容区域 -->
          <v-card-text>
            <!-- 搜索结果 -->
            <v-fade-transition>
              <div v-if="searchTeamsData.length" class="search-results">
                <div class="d-flex align-center mb-3">
                  <v-icon icon="mdi-magnify" color="primary" class="mr-2"></v-icon>
                  <span class="text-h6">{{ t('teams.explore.results') }}</span>
                  <v-spacer></v-spacer>
                  <span class="text-body-2 text-medium-emphasis">{{
                    t('teams.explore.resultCount', searchTeamsData.length)
                  }}</span>
                </div>
                <v-list class="team-list pa-0" rounded="md">
                  <v-list-item
                    v-for="team in searchTeamsData"
                    :key="team.id"
                    :title="team.name"
                    :subtitle="team.intro"
                    :prepend-avatar="getAvatarUrl(team.avatarId)"
                    :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }"
                    rounded="md"
                    class="team-list-item mb-3"
                  >
                  </v-list-item>
                </v-list>
              </div>
            </v-fade-transition>

            <BaseEmptyState
              v-if="hasSearched && !searchTeamsData.length"
              icon="mdi-account-search-outline"
              :title="t('teams.explore.noResults')"
              :desc="t('teams.explore.noResultsHint')"
            />

            <!-- 默认内容 - 招募广场 -->
            <div v-if="!hasSearched && !searchTeamsData.length" class="default-content">
              <div class="feature-section recruitment-section">
                <div class="text-h6 mb-4 d-flex align-center flex-row gap-2">
                  {{ t('teams.explore.recruitment') }}
                  <v-chip color="warning" variant="tonal" size="small" prepend-icon="mdi-clock-outline">
                    {{ t('teams.explore.comingSoon') }}
                  </v-chip>
                </div>
                <div class="feature-preview pa-6 rounded-lg text-center">
                  <v-avatar size="64" class="mb-3" color="primary" variant="tonal">
                    <v-icon icon="mdi-sign-caution"></v-icon>
                  </v-avatar>
                  <p class="text-subtitle-2 font-weight-medium text-center mb-1">{{ t('teams.explore.stayTuned') }}</p>
                  <p class="text-body-2 text-medium-emphasis mb-0">
                    {{ t('teams.explore.recruitmentHint') }}
                  </p>
                </div>
              </div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { ref } from 'vue'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'

const { mdAndUp } = useDisplay()
const searchQuery = ref('')
const searchTeamsData = ref<Team[]>([])
const hasSearched = ref(false)

const fetchSearchResults = async (query: string) => {
  if (!query) {
    searchTeamsData.value = []
    hasSearched.value = false
    return
  }

  hasSearched.value = true
  try {
    const {
      data: { teams },
    } = await TeamsApi.search({ query })
    searchTeamsData.value = teams
  } catch (error) {
    toast.error(t('teams.explore.searchFailed'))
    console.error(error)
  }
}
</script>

<style scoped>
.main-card {
  overflow: hidden;
}

.border {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.search-field {
  transition: opacity 0.3s ease;
}

.search-field:deep(.v-field__outline) {
  opacity: 0.7;
}

.search-field:hover:deep(.v-field__outline) {
  opacity: 1;
}

.team-list-item {
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease;
  margin-bottom: 8px;
  border: 1px solid transparent;
}

.team-list-item:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
  border-color: rgba(var(--v-theme-primary), 0.1);
}

.primary-gradient {
  background: linear-gradient(135deg, var(--v-theme-primary), var(--v-theme-primary-darken-1));
}

.create-btn {
  transition: transform 0.2s ease;
  box-shadow: var(--shadow-1);
}

.create-btn:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2);
}

.team-description-editor {
  min-height: 150px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
}

.create-team-dialog:deep(.v-card-text) {
  scrollbar-width: thin;
}

.teams-explore-header-container {
  position: relative;
  top: calc(-1 * var(--app-page-header-height));
  margin-bottom: calc(var(--app-page-header-height) * -1);
}

.teams-explore-header {
  padding-top: var(--app-page-header-height);
}

.default-content {
  min-height: 300px;
}
</style>
