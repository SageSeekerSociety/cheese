<script setup lang="ts">
// 小队的项目 (项目归团队, v4): every project this team owns — the default tab of
// the team page, a person's own projects included (they sit in a team of one). 新建项目
// opens the app-wide dialog with this team preselected: a project cannot be
// renamed once made, so it has to get its name here, and it has to land in
// THIS team or the rest of the team never sees it.
import type { Project } from '@/cx_types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { useProjectMenu } from '@/composables/useProjectMenu'
import { useRowMenu } from '@/composables/useRowMenu'

import TeamProjectsView from './TeamProjectsView.vue'

import { listProjects } from '@/api'
import i18n, { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const { locale } = i18n.global
const router = useRouter()
// 右键一张项目卡片，弹的是 rail 上右键那一格的同一份菜单。
const { projectMenu } = useProjectMenu(router)
const rowMenu = useRowMenu<string>()
// The URL names the team by handle; its id comes from the team the page loaded.
const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)

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

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(locale.value, { year: 'numeric', month: '2-digit', day: '2-digit' })
}

onMounted(load)
watch(teamId, load)
</script>

<template>
  <TeamProjectsView
    v-model:error="error"
    :team-data="teamData"
    :loading="loading"
    :projects="projects"
    :row-menu="rowMenu"
    :project-menu="projectMenu"
    :fmt-date="fmtDate"
    @new-project="newProject"
    @open="open"
  />
</template>
