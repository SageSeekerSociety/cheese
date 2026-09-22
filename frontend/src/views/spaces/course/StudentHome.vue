<template>
  <!--
    我的课程（学生）。他一进这门课要看到两件事：**这一周要做什么**，和**我这门课
    做到哪了**。没有题目、没有报名、没有算力 —— 那些词在学生这一侧不存在。

    「这一周」来自老师发布过的教学单元（接口只给发布过的），作业那一格指回那道题
    本来的提交入口 —— 提交与评审还是现成的那一套，学生这一侧不出现「题」这个字。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-3">{{ t('spaces.course.myCourse.title') }}</h1>

    <v-alert v-if="isPending" type="info" variant="tonal" density="comfortable" class="mb-4">
      {{ t('spaces.course.pending.body') }}
    </v-alert>

    <v-sheet flat rounded="lg" class="section pa-4 mb-4">
      <div class="text-subtitle-2 mb-2">{{ t('spaces.course.myCourse.thisWeek') }}</div>

      <div v-if="loadingWeek" class="text-center pa-2">
        <v-progress-circular indeterminate size="22" color="primary"></v-progress-circular>
      </div>

      <template v-else-if="currentUnit">
        <div class="d-flex align-baseline mb-1">
          <span class="text-caption text-medium-emphasis me-2">
            {{ t('spaces.course.myCourse.week', { week: currentUnit.week }) }}
          </span>
          <span class="text-body-1">{{ currentUnit.title }}</span>
        </div>
        <p v-if="currentUnit.summary" class="text-medium-emphasis mb-2">{{ currentUnit.summary }}</p>
        <div class="text-caption text-medium-emphasis mb-3">{{ unitDueLabel }}</div>
        <v-btn
          v-if="currentUnit.assignmentTaskId"
          color="primary"
          variant="tonal"
          :to="{
            name: 'SpacesDetailTasksDetail',
            params: { spaceId, taskId: currentUnit.assignmentTaskId },
          }"
        >
          {{ t('spaces.course.myCourse.openWork') }}
        </v-btn>
        <p v-else class="text-medium-emphasis mb-0">
          {{ t('spaces.course.myCourse.nothingToHandIn') }}
        </p>
      </template>

      <p v-else class="text-medium-emphasis mb-0">{{ t('spaces.course.myCourse.thisWeekEmpty') }}</p>
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
import type { SpaceMyParticipation, TeachingUnit } from '@/network/api/spaces/types'

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
const units = ref<TeachingUnit[]>([])
const loadingWeek = ref(true)

// 「这一周」= 已发布里周次最大的那个。接口只把发布过的给学生（未发布的他根本查不
// 到），所以这里不用再判一次发布状态；按 week 取最大而不是按 publishedAt —— 课程的
// 次序是周次，发布先后只是老师什么时候按的按钮。
const currentUnit = computed<TeachingUnit | null>(() => {
  if (!units.value.length) return null
  return units.value.reduce((latest, unit) => (unit.week > latest.week ? unit : latest))
})

const unitDueLabel = computed(() => {
  const unit = currentUnit.value
  if (!unit) return ''
  return unit.dueAt
    ? t('spaces.course.myCourse.due', { date: new Date(unit.dueAt).toLocaleString() })
    : t('spaces.course.myCourse.noDue')
})

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
    loadingWeek.value = false
    return
  }
  try {
    const { data } = await SpacesApi.getMyParticipations(id, { sortBy: 'latestSubmissionAt', sortOrder: 'desc' })
    participations.value = data.participations
  } catch {
    // 板子没过审、或者他其实还没有项目：这一格就是空的。
    participations.value = []
  }
  try {
    // 这里拿到的只有老师发布过的单元 —— 接口那一侧就只给发布过的。
    const { data } = await SpacesApi.listUnits(id)
    units.value = data.units
  } catch {
    units.value = []
  }
  loading.value = false
  loadingWeek.value = false
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}
</style>
