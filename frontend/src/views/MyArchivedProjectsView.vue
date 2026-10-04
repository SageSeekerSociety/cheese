<script setup lang="ts">
// 已归档的项目：归档了的项目不在任何列表里，只在它的所有者这里列出来，所有者在这里
// 把它取消归档。别人归档的、你只是成员的那些不在这里——能取消归档的只有所有者。
import type { Project } from '../cx_types'

import { onMounted, ref } from 'vue'

import { listArchivedProjects, unarchiveProject } from '../api'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import i18n, { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const store = useWorkspaceStore()
const projects = ref<Project[]>([])
const loading = ref(false)
const error = ref('')
const restoring = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    projects.value = (await listArchivedProjects()).data
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('project.archived.loadFailed')
  } finally {
    loading.value = false
  }
}

async function restore(project: Project) {
  restoring.value = project.id
  error.value = ''
  try {
    await unarchiveProject(project.id)
    projects.value = projects.value.filter((p) => p.id !== project.id)
    // 它回到项目清单里了；刷不成功不该把取消归档变成失败。
    await Promise.allSettled([store.refreshProjects()])
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('project.archived.unarchiveFailed')
  } finally {
    restoring.value = ''
  }
}

function archivedOn(project: Project): string {
  return project.archived_at ? new Date(project.archived_at).toLocaleDateString(i18n.global.locale.value) : ''
}

useCommands(() => [
  {
    id: 'archived-projects.refresh',
    title: t('project.archived.refresh'),
    icon: 'mdi-refresh',
    loading: loading.value,
    header: {},
    run: load,
  },
])

onMounted(load)
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
        <BaseButton kind="secondary" size="sm" :loading="restoring === p.id" @click="restore(p)">
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
