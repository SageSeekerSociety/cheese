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

import { listProjects } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const { locale } = i18n.global
const router = useRouter()
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
  <v-container class="px-6 py-4" fluid>
    <div class="mb-4 d-flex align-start">
      <div>
        <p class="text-body-2 text-medium-emphasis mb-0">
          {{ t(teamData?.personal ? 'teams.projects.ownSubtitle' : 'teams.projects.subtitle') }}
        </p>
      </div>
      <v-spacer />
      <BaseButton kind="primary" prepend-icon="mdi-plus" @click="newProject">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </div>

    <div v-if="loading" class="py-10 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <div v-else-if="projects.length === 0" class="text-center py-12">
      <v-icon icon="mdi-rocket-launch-outline" size="56" class="mb-3 empty-state-icon" />
      <h3 class="text-subtitle-1 font-weight-medium mb-1">{{ t('teams.projects.emptyTitle') }}</h3>
      <p class="text-body-2 text-medium-emphasis mb-4">{{ t('teams.projects.emptyHint') }}</p>
      <BaseButton kind="secondary" prepend-icon="mdi-plus" @click="newProject">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </div>

    <v-row v-else>
      <v-col v-for="p in projects" :key="p.id" cols="12" sm="6" lg="4">
        <v-card variant="outlined" rounded="lg" class="pa-4 fill-height project-card" @click="open(p)">
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
/* 空状态插图：元信息级别的装饰，--line-2 在浅色下 ≈ 原来的 grey-lighten-2，
   深色下是 #3A3E45，仍看得出形状。 */
.empty-state-icon {
  color: var(--line-2);
}
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
