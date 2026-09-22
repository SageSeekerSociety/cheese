<template>
  <!--
    我的课程（学生）。他一进这门课要看到两件事：**这一周要做什么**，和**我这门课
    做到哪了**。没有题目、没有报名、没有算力 —— 那些词在学生这一侧不存在。

    本周任务那一格今天只有容器与空态：教学单元是另一条任务（课程的时间线），它
    会把这里填上。留空态而不是编一条假作业，是因为假的那条会被当成真的。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-3">{{ t('spaces.course.myCourse.title') }}</h1>

    <v-alert v-if="isPending" type="info" variant="tonal" density="comfortable" class="mb-4">
      {{ t('spaces.course.pending.body') }}
    </v-alert>

    <v-sheet flat rounded="lg" class="section pa-4 mb-4">
      <div class="text-subtitle-2 mb-2">{{ t('spaces.course.myCourse.thisWeek') }}</div>
      <p class="text-medium-emphasis mb-0">{{ t('spaces.course.myCourse.thisWeekEmpty') }}</p>
    </v-sheet>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else>
      <div class="text-subtitle-2 mb-2">{{ t('spaces.course.myCourse.progress') }}</div>
      <v-list v-if="participations.length" rounded="lg">
        <v-list-item
          v-for="item in participations"
          :key="item.participationId"
          :to="{ name: 'SpacesDetailTasksDetail', params: { spaceId, taskId: item.taskId } }"
        >
          <v-list-item-title>{{ item.taskName }}</v-list-item-title>
          <v-list-item-subtitle>{{ statusLabel(item) }}</v-list-item-subtitle>
          <template #append>
            <v-chip size="small" variant="tonal" :color="statusColor(item)">
              {{ statusLabel(item) }}
            </v-chip>
          </template>
        </v-list-item>
      </v-list>

      <v-sheet v-else flat rounded="lg" class="section pa-6 text-center">
        <p class="text-medium-emphasis mb-0">{{ t('spaces.course.myCourse.noWork') }}</p>
      </v-sheet>
    </template>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyParticipation } from '@/network/api/spaces/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { currentSpace: space } = storeToRefs(useSpaceStore())

const spaceId = String(route.params.spaceId)
const loading = ref(true)
const participations = ref<SpaceMyParticipation[]>([])

const isPending = computed(() => space.value?.reviewStatus === 'PENDING')

// 完成状态是服务端算的（`SpaceMyParticipation.completionStatus`），前端只负责
// 把它翻成人话 —— 自己不另算一套「做完了没有」。
const STATUS_LABEL: Record<string, string> = {
  NOT_SUBMITTED: 'spaces.course.myCourse.status.notSubmitted',
  PENDING_REVIEW: 'spaces.course.myCourse.status.pendingReview',
  REJECTED_RESUBMITTABLE: 'spaces.course.myCourse.status.resubmittable',
  FAILED: 'spaces.course.myCourse.status.failed',
  SUCCESS: 'spaces.course.myCourse.status.success',
}

function statusLabel(item: SpaceMyParticipation): string {
  const key = STATUS_LABEL[item.completionStatus]
  return key ? t(key) : t('spaces.course.myCourse.status.unknown')
}

function statusColor(item: SpaceMyParticipation): string {
  if (item.completionStatus === 'SUCCESS') return 'success'
  if (item.completionStatus === 'NOT_SUBMITTED') return 'warning'
  return 'primary'
}

onMounted(async () => {
  const id = Number(spaceId)
  if (!Number.isFinite(id) || id <= 0) {
    loading.value = false
    return
  }
  try {
    const { data } = await SpacesApi.getMyParticipations(id, { sortBy: 'latestSubmissionAt', sortOrder: 'desc' })
    participations.value = data.participations
  } catch {
    // 板子没过审、或者他其实还没有项目：这一格就是空的。
    participations.value = []
  }
  loading.value = false
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}
</style>
