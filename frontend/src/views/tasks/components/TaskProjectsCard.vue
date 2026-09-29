<template>
  <!-- 从这道题开出来的项目。先列已有的，再给「新建」：直接给一颗会默默再建一个的按钮，
       人会建出第二个、第三个同样的项目。 -->
  <PanelCard title="项目">
    <p v-if="loading" class="tp__note">加载中</p>
    <div v-else-if="failed" class="tp__note">
      项目列表加载失败
      <v-btn variant="text" size="small" @click="load">重试</v-btn>
    </div>
    <ul v-else-if="projects.length" class="tp__list">
      <li v-for="p in projects" :key="p.id">
        <router-link :to="`/projects/${p.id}`" class="tp__link">
          <v-icon size="18">mdi-folder-outline</v-icon>
          <span>{{ p.name }}</span>
        </router-link>
      </li>
    </ul>
    <p v-else class="tp__note">暂无项目</p>

    <v-btn variant="tonal" prepend-icon="mdi-plus" class="mt-3" @click="create">用这道题新建项目</v-btn>
  </PanelCard>
</template>

<script setup lang="ts">
import type { Project } from '@/cx_types'
import type { TaskParticipationInfo } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { ref, watch } from 'vue'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listProjectsForTask } from '@/api'
import PanelCard from '@/components/spaces/PanelCard.vue'

const props = defineProps<{
  task: Task
  participationInfo: TaskParticipationInfo | null
}>()

const projects = ref<Project[]>([])
const loading = ref(false)
const failed = ref(false)
const { show: showNewProjectDialog } = useNewProjectDialog()

async function load() {
  loading.value = true
  failed.value = false
  try {
    projects.value = (await listProjectsForTask(props.task.id)).data
  } catch {
    projects.value = []
    failed.value = true
  } finally {
    loading.value = false
  }
}

/** 这道题我只用一个团队领了的话，新项目就挂在那个团队下；否则让人在对话框里选。 */
function create() {
  const teams =
    props.participationInfo?.identities.filter((i) => i.type === 'TEAM' && i.approved !== 'DISAPPROVED') ?? []
  showNewProjectDialog(teams.length === 1 ? teams[0].memberId : null, { id: props.task.id, name: props.task.name })
}

watch(() => props.task.id, load, { immediate: true })
</script>

<style scoped>
.tp__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.tp__link {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  color: var(--ink);
  text-decoration: none;
}

.tp__link:hover {
  color: var(--accent-ink);
}

.tp__note {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
</style>
