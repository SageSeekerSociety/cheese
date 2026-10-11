<script setup lang="ts">
// 已归档的项目：归档了的项目不在任何列表里，只在它的所有者这里列出来，所有者在这里
// 把它取消归档。别人归档的、你只是成员的那些不在这里——能取消归档的只有所有者。
//
// 容器：取列表、调后端取消归档、刷新项目清单。画面在
// `MyArchivedProjectsViewView.vue`，只收 props、只发 `restore`。
import type { Project } from '../cx_types'

import { onMounted, ref } from 'vue'

import { listArchivedProjects, unarchiveProject } from '../api'
import { t } from '../i18n'

import MyArchivedProjectsViewView from './MyArchivedProjectsViewView.vue'

import { useCommands } from '@/commands'
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
  <MyArchivedProjectsViewView
    :projects="projects"
    :loading="loading"
    :error="error"
    :restoring="restoring"
    @restore="restore"
  />
</template>
