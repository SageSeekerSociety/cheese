<script setup lang="ts">
// 小队的项目这一块**画的那一半**：项目卡片墙、空状态、读不到的告警、右键菜单。
//
// 取数（`listProjects`）、右键菜单里的去处（复制链接、打开设置、退出）都在容器
// `TeamProjects.vue` 里；这里只吃 props，新建/打开项目往上发事件，右键菜单那份清单
// 由容器算好当回调传进来。行菜单自己的开合是纯 UI 状态，留在这里。
import type { MenuAction } from '@/components/common/menuAction'
import type { Project } from '@/cx_types'

import { computed } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import i18n, { t } from '@/i18n'

const props = defineProps<{
  projects: Project[]
  loading: boolean
  /** 读失败时的说明行；非空就画告警。 */
  error: string | null
  /** 这个团队是不是「一个人的团队」，决定副标题说哪一句。 */
  personal: boolean
  /** 一张项目卡片的右键菜单清单，由容器按当前项目算好。 */
  projectMenu: (project: Project) => MenuAction[]
}>()

defineEmits<{
  newProject: []
  open: [project: Project]
  clearError: []
}>()

const { locale } = i18n.global
const rowMenu = useRowMenu<string>()

const subtitle = computed(() => t(props.personal ? 'teams.projects.ownSubtitle' : 'teams.projects.subtitle'))

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(locale.value, { year: 'numeric', month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div class="mb-4 d-flex align-start">
      <div>
        <p class="text-body-2 text-medium-emphasis mb-0">{{ subtitle }}</p>
      </div>
      <v-spacer />
      <BaseButton kind="primary" prepend-icon="mdi-plus" @click="$emit('newProject')">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </div>

    <div v-if="loading" class="py-10 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <v-alert
      v-else-if="error"
      type="error"
      density="comfortable"
      class="mb-4"
      closable
      @click:close="$emit('clearError')"
    >
      {{ error }}
    </v-alert>

    <BaseEmptyState
      v-else-if="projects.length === 0"
      icon="mdi-rocket-launch-outline"
      :title="t('teams.projects.emptyTitle')"
      :desc="t('teams.projects.emptyHint')"
    >
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" class="mt-4" @click="$emit('newProject')">{{
        t('teams.projects.newProject')
      }}</BaseButton>
    </BaseEmptyState>

    <v-row v-else>
      <v-col v-for="p in projects" :key="p.id" cols="12" sm="6" lg="4">
        <v-card
          variant="outlined"
          rounded="lg"
          class="pa-4 fill-height project-card"
          @click="$emit('open', p)"
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
