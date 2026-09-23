<template>
  <!--
    课程总览（老师）。数的是「这门课现在有什么在等我」：多少人、多少个作业单元、
    多少人在等我验收。数字全部来自现成接口 —— 拿不到就整块不出现，**不编一个 0**：
    屏幕上那个 0 会被当成真的。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <div class="d-flex align-center flex-wrap ga-3 mb-3">
      <h1 class="text-h6">{{ t('spaces.course.overview.title') }}</h1>
      <v-chip v-if="isPending" size="small" color="warning" variant="tonal">
        {{ t('spaces.course.pending.chip') }}
      </v-chip>
    </div>

    <!-- 还在审核中的板子：子资源一律 404（`require_reviewed_space` 只对创建者放行
         `GET /spaces/{id}` 本身），所以这里必须说清为什么是空的，而不是画一张
         「暂无数据」让人以为坏了。 -->
    <v-alert v-if="isPending" type="info" variant="tonal" density="comfortable" class="mb-4">
      {{ t('spaces.course.pending.body') }}
    </v-alert>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else>
      <v-row v-if="stats.length" dense>
        <v-col v-for="stat in stats" :key="stat.label" cols="6" md="3">
          <v-sheet flat rounded="lg" class="stat pa-4">
            <div class="text-h5">{{ stat.value }}</div>
            <div class="text-medium-emphasis text-body-2">{{ stat.label }}</div>
          </v-sheet>
        </v-col>
      </v-row>
      <v-alert v-else type="info" variant="tonal" density="comfortable" class="mb-4">
        {{ t('spaces.course.overview.noNumbers') }}
      </v-alert>

      <div class="d-flex flex-wrap ga-2">
        <v-btn
          v-for="cell in teacherCells"
          :key="cell.route"
          variant="tonal"
          color="primary"
          :prepend-icon="cell.icon"
          :to="{ name: cell.route, params: { spaceId } }"
        >
          {{ t(cell.label) }}
        </v-btn>
      </div>
    </template>
  </v-sheet>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { COURSE_TEACHER_CELLS, visibleCourseCells } from '@/lib/courseNav'
import { SpacesApi } from '@/network/api/spaces'
import { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/network/api/spaces/types'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { currentSpace: space } = storeToRefs(useSpaceStore())

const spaceId = String(route.params.spaceId)
// 这几个入口跟侧栏同一份声明、同一个筛选：关掉的模块在这里也不出现。
const teacherCells = computed(() => visibleCourseCells(COURSE_TEACHER_CELLS, space.value?.courseModules))
const loading = ref(true)

// 三个数字各来自一个接口，一块坏掉不连坐另外两块。
const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
const memberCount = ref<number | null>(null)

const isPending = computed(() => space.value?.reviewStatus === 'PENDING')

const stats = computed(() => {
  const rows: { label: string; value: number }[] = []
  if (memberCount.value !== null) {
    rows.push({ label: t('spaces.course.overview.members'), value: memberCount.value })
  }
  if (overview.value) {
    rows.push(
      {
        label: t('spaces.course.overview.units'),
        value: overview.value.entityMetrics.taskCount,
      },
      {
        label: t('spaces.course.overview.students'),
        value: overview.value.entityMetrics.participantCount,
      }
    )
  }
  if (alerts.value) {
    rows.push({
      label: t('spaces.course.overview.awaitingReview'),
      value: alerts.value.pendingSubmissionReviewCount,
    })
  }
  return rows
})

onMounted(async () => {
  const id = Number(spaceId)
  if (!Number.isFinite(id) || id <= 0) {
    loading.value = false
    return
  }
  const [members, over, al] = await Promise.allSettled([
    SpacesApi.listMembers(id),
    SpacesApi.getAnalyticsOverview(id),
    SpacesApi.getAnalyticsAlerts(id),
  ])
  if (members.status === 'fulfilled') memberCount.value = members.value.data.members.length
  if (over.status === 'fulfilled') overview.value = over.value.data
  if (al.status === 'fulfilled') alerts.value = al.value.data
  loading.value = false
})
</script>

<style scoped lang="scss">
.stat {
  border: 1px solid var(--line);
}
</style>
