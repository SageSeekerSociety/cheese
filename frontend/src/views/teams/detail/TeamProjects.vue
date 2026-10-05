<template>
  <TeamProjectsView
    :projects="projects"
    :loading="loading"
    :error="error"
    :personal="personal"
    :project-menu="projectMenu"
    @new-project="newProject"
    @open="open"
    @clear-error="error = null"
  />
</template>

<script setup lang="ts">
// 小队的项目 (项目归团队, v4): every project this team owns — the default tab of
// the team page, a person's own projects included (they sit in a team of one). 新建项目
// opens the app-wide dialog with this team preselected: a project cannot be
// renamed once made, so it has to get its name here, and it has to land in
// THIS team or the rest of the team never sees it.
//
// 这里只留取数和去处：`listProjects`、`useRouter`、右键菜单、新建项目对话框。
// 画的那一半在 `TeamProjectsView.vue`。
import type { Project } from '@/cx_types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { useProjectMenu } from '@/composables/useProjectMenu'

import TeamProjectsView from './TeamProjectsView.vue'

import { listProjects } from '@/api'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const router = useRouter()
// 右键一张项目卡片，弹的是 rail 上右键那一格的同一份菜单。
const { projectMenu } = useProjectMenu(router)
// The URL names the team by handle; its id comes from the team the page loaded.
const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)
const personal = computed(() => !!teamData.value?.personal)

const projects = ref<Project[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const { show: showNewProjectDialog } = useNewProjectDialog()

async function load() {
  loading.value = true
  error.value = null
  try {
    projects.value = (await listProjects(teamId.value)).data
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('teams.projects.loadFailed')
  } finally {
    loading.value = false
  }
}

function newProject() {
  showNewProjectDialog(teamId.value)
}

function open(p: Project) {
  router.push(`/project/${p.id}`)
}

onMounted(load)
watch(teamId, load)
</script>
