<template>
  <div class="project-header mb-4">
    <div class="d-flex flex-wrap align-center gap-4 mb-4">
      <div class="d-flex align-center">
        <div class="project-color-indicator mr-3" :style="{ backgroundColor: project?.colorCode || '#f97316' }"></div>
        <h1 class="text-h4 font-weight-medium">{{ project?.name || '加载中...' }}</h1>
      </div>
      <v-spacer></v-spacer>
      <div v-if="project" class="d-flex align-center flex-wrap gap-2">
        <v-btn
          variant="outlined"
          rounded="pill"
          prepend-icon="mdi-pencil"
          class="project-action-btn"
          @click="emit('edit-project')"
        >
          编辑项目
        </v-btn>
      </div>
    </div>

    <!-- 项目基本信息 -->
    <v-card variant="flat" rounded="lg" class="project-info-card mb-4">
      <v-card-text class="pa-5">
        <v-row>
          <v-col cols="12" md="8">
            <div class="project-description mb-4">
              <p class="text-body-1">{{ project?.description || '加载中...' }}</p>
            </div>

            <div class="project-meta d-flex flex-wrap gap-y-3">
              <div class="project-meta-item">
                <div class="text-caption text-medium-emphasis mb-1">时间范围</div>
                <div class="d-flex align-center">
                  <v-icon size="small" color="on-surface-variant" class="mr-1">mdi-calendar-range</v-icon>
                  <span class="text-body-2">{{ formatDateRange(project?.startDate, project?.endDate) }}</span>
                </div>
              </div>

              <div class="project-meta-item">
                <div class="text-caption text-medium-emphasis mb-1">负责人</div>
                <div class="d-flex align-center">
                  <v-icon size="small" color="on-surface-variant" class="mr-1">mdi-account</v-icon>
                  <span class="text-body-2">{{ project?.leader?.nickname || '未指定' }}</span>
                </div>
              </div>

              <div v-if="project?.githubRepo" class="project-meta-item">
                <div class="text-caption text-medium-emphasis mb-1">GitHub 仓库</div>
                <div class="d-flex align-center">
                  <v-icon size="small" color="on-surface-variant" class="mr-1">mdi-github</v-icon>
                  <a
                    :href="`https://github.com/${project.githubRepo}`"
                    target="_blank"
                    class="text-body-2 text-decoration-none"
                  >
                    {{ project.githubRepo }}
                  </a>
                </div>
              </div>
            </div>
          </v-col>

          <v-col cols="12" md="4">
            <div class="status-overview d-flex flex-column h-100 justify-center">
              <div class="status-cards d-flex flex-wrap gap-3">
                <div class="status-card">
                  <div class="status-value">{{ project?.members?.count || 0 }}</div>
                  <div class="status-label">成员</div>
                </div>

                <div class="status-card">
                  <div class="status-value">{{ subprojectsCount }}</div>
                  <div class="status-label">子项目</div>
                </div>

                <div class="status-card">
                  <div class="status-value">0</div>
                  <div class="status-label">讨论</div>
                </div>
              </div>
            </div>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 分割线 -->
    <v-divider class="mb-4"></v-divider>
  </div>
</template>

<script setup lang="ts">
import type { Project } from '@/types'

import { computed, defineEmits, defineProps } from 'vue'
import dayjs from 'dayjs'

const props = defineProps<{
  project: Project | null
  subprojects: Project[]
}>()

const emit = defineEmits(['edit-project'])

const subprojectsCount = computed(() => props.subprojects?.length || 0)

// 格式化日期范围
const formatDateRange = (startDate?: number, endDate?: number) => {
  if (!startDate || !endDate) return '未设置'
  const start = dayjs(startDate).format('YYYY/MM/DD')
  const end = dayjs(endDate).format('YYYY/MM/DD')
  return `${start} - ${end}`
}
</script>

<style scoped lang="scss">
// 项目头部样式
.project-header {
  position: relative;
}

/* 项目标记色是【用户数据】：用户给项目挑的颜色存在 colorCode 里，两个主题下
   必须是同一个颜色，所以这个兜底值不 token 化（design-system §1.2 例外）。 */
.project-color-indicator {
  width: 10px;
  height: 36px;
  border-radius: 4px;
}

.project-action-btn {
  transition: transform 0.2s ease;

  &:hover {
    transform: translateY(-2px);
  }
}

// 项目信息卡片样式
.project-info-card {
  border: 1px solid var(--line);
  background-color: rgba(var(--v-theme-surface), 0.8);
  backdrop-filter: blur(10px);
}

.project-description {
  color: var(--text);
  line-height: 1.6;
}

.project-meta {
  &-item {
    margin-right: 2.5rem;
    margin-bottom: 0.5rem;
  }
}

// 状态卡片样式
.status-overview {
  @media (min-width: 960px) {
    border-left: 1px solid var(--line);
    padding-left: 2rem;
  }
}

.status-cards {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 1rem;

  @media (min-width: 960px) {
    justify-content: flex-start;
  }
}

.status-card {
  background-color: var(--fill);
  border-radius: 8px;
  padding: 12px 16px;
  min-width: 80px;
  text-align: center;
  transition: all 0.3s ease;

  &:hover {
    background-color: var(--fill-2);
    transform: translateY(-2px);
  }

  .status-value {
    font-size: 24px;
    font-weight: 600;
    color: rgb(var(--v-theme-primary));
    line-height: 1.2;
  }

  .status-label {
    font-size: 14px;
    color: var(--muted);
    margin-top: 4px;
  }
}
</style>
