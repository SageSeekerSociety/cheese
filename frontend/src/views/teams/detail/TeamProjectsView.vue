<!--
  The rendering half of TeamProjects.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the requests and the stores.
-->

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type { Project } from '@/cx_types'
import type { Team } from '@/types/teams'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'

defineProps<{
  teamData: Team | undefined
  loading: boolean
  projects: Project[]
  rowMenu: {
    open: (key: string, event: MouseEvent) => void
    bind: (key: string) => {
      modelValue: boolean
      point: [number, number] | null
      'onUpdate:modelValue': (value: boolean) => void
    }
  }
  projectMenu: (project: Project) => MenuAction[]
  fmtDate: (iso: string) => string
}>()
const emit = defineEmits<{
  'new-project': []
  open: [p: Project]
}>()
const error = defineModel<string | null>('error', { required: true })
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div class="mb-4 d-flex align-start">
      <div>
        <p class="text-body-2 text-medium-emphasis mb-0">
          {{ t(teamData?.personal ? 'teams.projects.ownSubtitle' : 'teams.projects.subtitle') }}
        </p>
      </div>
      <v-spacer />
      <BaseButton kind="primary" prepend-icon="mdi-plus" @click="emit('new-project')">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </div>

    <div v-if="loading" class="py-10 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <BaseEmptyState
      v-else-if="projects.length === 0"
      icon="mdi-rocket-launch-outline"
      :title="t('teams.projects.emptyTitle')"
      :desc="t('teams.projects.emptyHint')"
    >
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" class="mt-4" @click="emit('new-project')">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </BaseEmptyState>

    <v-row v-else>
      <v-col v-for="p in projects" :key="p.id" cols="12" sm="6" lg="4">
        <v-card
          variant="outlined"
          rounded="lg"
          class="pa-4 fill-height project-card"
          @click="emit('open', p)"
          @contextmenu="rowMenu.open(p.id, $event)"
        >
          <AdaptiveMenu v-bind="rowMenu.bind(p.id)" :actions="projectMenu(p)" :title="p.name">
            <template #activator />
          </AdaptiveMenu>
          <div class="d-flex align-center mb-1">
            <v-icon size="20" color="primary" class="mr-2">mdi-robot-happy-outline</v-icon>
            <span class="text-subtitle-2 font-weight-medium text-truncate">{{ p.name }}</span>
          </div>
          <div class="text-caption text-medium-emphasis">
            {{ t('teams.projects.createdAt', { date: fmtDate(p.created_at) }) }}
          </div>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.project-card {
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s;
}
.project-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  background: rgba(var(--v-theme-primary), 0.03);
}
</style>
