<script setup lang="ts">
// 小队的项目 (项目归团队, v4): every project this team owns — the default tab of
// the team page, personal team included (个人项目 = 个人团队的项目). 新建项目
// opens the app-wide dialog with this team preselected: a project cannot be
// renamed once made, so it has to get its name here, and it has to land in
// THIS team or the rest of the team never sees it.
import type { Project } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listProjects } from '@/api'

const route = useRoute()
const router = useRouter()
const teamId = computed(() => Number(route.params.teamId))

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
    error.value = e instanceof Error ? e.message : '加载项目失败'
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
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

onMounted(load)
watch(teamId, load)
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div class="mb-4 d-flex align-start">
      <div>
        <h2 class="text-h6 font-weight-medium mb-1">项目</h2>
        <p class="text-body-2 text-medium-emphasis mb-0">
          这个小队的 AI 工作台。每个项目里和芝士开话题协作，产出与算力都归小队。
        </p>
      </div>
      <v-spacer />
      <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="newProject"> 新建项目 </v-btn>
    </div>

    <div v-if="loading" class="py-10 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <div v-else-if="projects.length === 0" class="text-center py-12">
      <v-icon icon="mdi-rocket-launch-outline" size="56" class="mb-3 empty-state-icon" />
      <h3 class="text-subtitle-1 font-weight-medium mb-1">还没有项目</h3>
      <p class="text-body-2 text-medium-emphasis mb-4">点「新建项目」直接开一个，进去就能和芝士开工。</p>
      <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="newProject"> 新建项目 </v-btn>
    </div>

    <v-row v-else>
      <v-col v-for="p in projects" :key="p.id" cols="12" sm="6" lg="4">
        <v-card variant="outlined" rounded="lg" class="pa-4 fill-height project-card" @click="open(p)">
          <div class="d-flex align-center mb-1">
            <v-icon size="20" color="primary" class="mr-2">mdi-robot-happy-outline</v-icon>
            <span class="text-subtitle-2 font-weight-medium text-truncate">{{ p.name }}</span>
          </div>
          <p v-if="p.summary" class="text-body-2 text-medium-emphasis summary mb-2">{{ p.summary }}</p>
          <div class="text-caption text-medium-emphasis">创建于 {{ fmtDate(p.created_at) }}</div>
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
.summary {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
