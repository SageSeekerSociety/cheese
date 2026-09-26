<template>
  <!--
    本周任务（成员）。和管理员的「作业与验收」是同一条路由（`course/assignments`），
    分叉在 CourseWork 里按 `space.admins` 判。管理员那一屏是收作业的队列，接口只给
    管理员 —— 成员要看的是「每一周我要做什么、做到哪了」：这一周排在最前，往前的
    周次按倒序跟在后面，每一周的作业带着它的完成状态，有小测就给小测的入口。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-3">{{ t('spaces.course.nav.thisWeek') }}</h1>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <p v-else-if="!weeks.length" class="text-medium-emphasis mb-0">{{ t('spaces.course.myCourse.thisWeekEmpty') }}</p>

    <template v-else>
      <v-sheet
        v-for="(unit, i) in weeks"
        :key="unit.id"
        flat
        rounded="lg"
        class="week pa-4 mb-3"
        :class="{ 'week--now': i === 0 }"
        :data-week="unit.week"
      >
        <div class="d-flex align-baseline flex-wrap ga-2 mb-1">
          <span class="text-caption text-medium-emphasis">{{
            t('spaces.course.myCourse.week', { week: unit.week })
          }}</span>
          <span class="text-body-1">{{ unit.title }}</span>
          <v-chip v-if="i === 0" size="x-small" color="primary" variant="tonal">{{
            t('spaces.course.weeks.now')
          }}</v-chip>
        </div>
        <p v-if="unit.summary" class="text-medium-emphasis mb-2">{{ unit.summary }}</p>
        <div class="text-caption text-medium-emphasis mb-3">
          {{
            unit.dueAt
              ? t('spaces.course.myCourse.due', { date: new Date(unit.dueAt).toLocaleString() })
              : t('spaces.course.myCourse.noDue')
          }}
        </div>
        <div class="d-flex align-center flex-wrap ga-2">
          <template v-if="unit.assignmentTaskId">
            <v-btn
              color="primary"
              variant="tonal"
              :to="{ name: 'SpacesDetailTasksDetail', params: { spaceId, taskId: unit.assignmentTaskId } }"
            >
              {{ t('spaces.course.weeks.openWork') }}
            </v-btn>
            <v-chip
              v-if="workOf(unit)"
              size="small"
              variant="tonal"
              :color="statusColor(workOf(unit)!)"
              data-testid="work-status"
            >
              {{ statusLabel(workOf(unit)!) }}
            </v-chip>
          </template>
          <v-btn
            v-if="unit.quizId"
            color="primary"
            variant="outlined"
            :to="{ name: 'SpacesCourseQuiz', params: { spaceId }, query: { unit: String(unit.id) } }"
          >
            {{ t('spaces.course.weeks.openQuiz') }}
          </v-btn>
          <span v-if="!unit.assignmentTaskId && !unit.quizId" class="text-medium-emphasis">
            {{ t('spaces.course.myCourse.nothingToHandIn') }}
          </span>
        </div>
      </v-sheet>
    </template>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyParticipation, TeachingUnit } from '@/network/api/spaces/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { courseWorkStatus } from '@/lib/courseNav'
import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const route = useRoute()

const spaceId = String(route.params.spaceId)
const loading = ref(true)
const units = ref<TeachingUnit[]>([])
const participations = ref<SpaceMyParticipation[]>([])

// 接口只给成员发布过的单元；周次越大越新，这一周（最大的那一周）排在最前。
const weeks = computed(() => [...units.value].sort((a, b) => b.week - a.week))

function workOf(unit: TeachingUnit): SpaceMyParticipation | undefined {
  return participations.value.find((p) => p.taskId === unit.assignmentTaskId)
}

function statusLabel(item: SpaceMyParticipation): string {
  return t(courseWorkStatus(item.completionStatus).label)
}

function statusColor(item: SpaceMyParticipation): string {
  return courseWorkStatus(item.completionStatus).color
}

onMounted(async () => {
  const id = Number(spaceId)
  if (!Number.isFinite(id) || id <= 0) {
    loading.value = false
    return
  }
  const [unitsResult, workResult] = await Promise.allSettled([
    SpacesApi.listUnits(id),
    SpacesApi.getMyParticipations(id, { sortBy: 'latestSubmissionAt', sortOrder: 'desc' }),
  ])
  units.value = unitsResult.status === 'fulfilled' ? unitsResult.value.data.units : []
  participations.value = workResult.status === 'fulfilled' ? workResult.value.data.participations : []
  loading.value = false
})
</script>

<style scoped lang="scss">
.week {
  border: 1px solid var(--line);
}
.week--now {
  border-color: rgb(var(--v-theme-primary));
}
</style>
