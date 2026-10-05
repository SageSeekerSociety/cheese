<template>
  <v-container class="teams-container" fluid>
    <v-row>
      <v-col cols="12">
        <v-card flat class="my-teams-card" :title="t('teams.mine.title')">
          <template #text>
            <!-- 加载状态 -->
            <div v-if="loading" class="d-flex flex-column align-center py-5">
              <v-progress-circular
                indeterminate
                color="primary"
                :size="40"
                :width="3"
                class="mb-3"
              ></v-progress-circular>
              <p class="text-body-2 text-medium-emphasis text-center">{{ t('teams.mine.loading') }}</p>
            </div>

            <!-- 错误状态 -->
            <div v-else-if="error" class="d-flex flex-column align-center py-5">
              <v-avatar size="50" class="mb-3 bg-error-lighten-5">
                <v-icon icon="mdi-alert-circle" size="large" color="error"></v-icon>
              </v-avatar>
              <p class="text-subtitle-1 font-weight-medium text-center mb-1">{{ t('teams.mine.loadFailed') }}</p>
              <p class="text-body-2 text-center text-medium-emphasis mb-3">{{ t('teams.mine.loadFailedHint') }}</p>
              <BaseButton kind="secondary" size="sm" prepend-icon="mdi-refresh" @click="$emit('reload')">
                {{ t('teams.mine.reload') }}
              </BaseButton>
            </div>

            <!-- 空状态 -->
            <div v-else-if="!teams.length" class="d-flex flex-column align-center py-5">
              <v-avatar size="50" class="mb-3 bg-surface-light">
                <v-icon icon="mdi-account-group" size="large" color="on-surface-variant"></v-icon>
              </v-avatar>
              <p class="text-subtitle-1 font-weight-medium text-center mb-1">{{ t('teams.mine.emptyTitle') }}</p>
              <p class="text-body-2 text-center text-medium-emphasis">{{ t('teams.mine.emptyHint') }}</p>
            </div>

            <!-- 小队列表 -->
            <v-list v-else class="my-teams-list pa-0">
              <v-list-item
                v-for="team in teams"
                :key="team.id"
                :title="team.name"
                :subtitle="team.intro"
                data-user-content
                :prepend-avatar="getAvatarUrl(team.avatarId)"
                :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }"
                rounded="md"
                class="my-team-item mb-2"
              >
                <template #append>
                  <v-icon icon="mdi-chevron-right" color="on-surface-variant"></v-icon>
                </template>
              </v-list-item>
            </v-list>
          </template>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
// 「我加入的小队」这一页**画的那一半**：加载、没读到、一个都没有、列出来，四种样子。
//
// 取数（`TeamsApi.getMyTeams`）在容器 `Mine.vue` 里；这里只吃 props，重试往上发。
import type { Team } from '@/types'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  teams: Team[]
  loading: boolean
  error: boolean
}>()

defineEmits<{
  reload: []
}>()
</script>

<style scoped>
.my-teams-card {
  overflow: hidden;
}

.border {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.my-team-item {
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease;
  border: 1px solid transparent;
}

.my-team-item:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
  border-color: rgba(var(--v-theme-primary), 0.1);
}

.bg-error-lighten-5 {
  background-color: rgba(var(--v-theme-error), 0.1);
}
</style>
