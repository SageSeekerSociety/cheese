<script setup lang="ts">
// The picture of the archived-projects page: the empty hint, the rows and the
// unarchive button. Unarchiving is not here — the container
// `MyArchivedProjectsView.vue` fetches the list, calls the backend and refreshes
// the project list. This half receives props and emits `restore`.
import type { Project } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import i18n, { t } from '@/i18n'

defineProps<{
  projects: Project[]
  loading: boolean
  error: string
  /** The id of the project being unarchived, or '' when none. */
  restoring: string
}>()

defineEmits<{
  restore: [project: Project]
}>()

function archivedOn(project: Project): string {
  return project.archived_at ? new Date(project.archived_at).toLocaleDateString(i18n.global.locale.value) : ''
}
</script>

<template>
  <AppPage :title="t('navigation.userMenu.archivedProjects')">
    <p v-if="error" role="alert" class="t-body c-danger mb-4">{{ error }}</p>
    <p v-if="!loading && !projects.length && !error" class="t-body c-muted">{{ t('project.archived.empty') }}</p>
    <ul class="archived__list">
      <li v-for="p in projects" :key="p.id" class="archived__row">
        <div class="archived__name">
          <div class="t-title text-truncate">{{ p.name }}</div>
          <div class="t-meta">{{ t('project.archived.on', { date: archivedOn(p) }) }}</div>
        </div>
        <BaseButton kind="secondary" size="sm" :loading="restoring === p.id" @click="$emit('restore', p)">
          {{ t('project.archived.unarchive') }}
        </BaseButton>
      </li>
    </ul>
  </AppPage>
</template>

<style scoped>
.archived__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.archived__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.archived__name {
  flex: 1;
  min-width: 0;
}
</style>
