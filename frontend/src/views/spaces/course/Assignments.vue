<template>
  <!--
    作业与验收（老师）。数的是「这门课收上来没有、还有谁没交、多少份等着我
    看」——一处请求拿整门课，不是逐道题 × 逐个学生地问。点开一行是那份提交
    本身，看与打分都走既有的提交 / 评审接口（这一屏不重写批改）。
  -->
  <div class="d-flex flex-column ga-4">
    <v-sheet flat rounded="lg" class="pa-4">
      <div class="d-flex align-center flex-wrap ga-3 mb-3">
        <h1 class="text-h6 mb-0">{{ t('spaces.course.assignments.title') }}</h1>
        <v-chip v-if="isPending" size="small" color="warning" variant="tonal">
          {{ t('spaces.course.pending.chip') }}
        </v-chip>
        <div class="flex-grow-1"></div>
        <v-btn-toggle v-model="scope" mandatory density="comfortable" variant="tonal">
          <v-btn :value="'pending'" @click="reload">
            {{ t('spaces.course.assignments.queue') }}
          </v-btn>
          <v-btn :value="'all'" @click="reload">{{ t('spaces.course.assignments.all') }}</v-btn>
        </v-btn-toggle>
      </div>

      <v-alert v-if="isPending" type="info" variant="tonal" density="comfortable" class="mb-3">
        {{ t('spaces.course.pending.body') }}
      </v-alert>

      <div v-if="loading" class="text-center pa-4">
        <v-progress-circular indeterminate color="primary"></v-progress-circular>
      </div>

      <template v-else>
        <!-- 数字全部来自同一个口径（每人只算最新一版）；拿不到就整块不出现，
             不编一个 0 —— 屏幕上那个 0 会被当成真的。 -->
        <v-row v-if="summary" dense class="mb-2">
          <v-col v-for="stat in stats" :key="stat.key" cols="6" md="3">
            <v-sheet flat rounded="lg" class="stat pa-4">
              <div class="text-h5">{{ stat.value }}</div>
              <div class="text-medium-emphasis text-body-2">{{ t(stat.label) }}</div>
            </v-sheet>
          </v-col>
        </v-row>

        <v-empty-state
          v-if="!rows.length"
          :title="t('spaces.course.assignments.emptyTitle')"
          :text="t('spaces.course.assignments.emptyBody')"
          icon="mdi-inbox-outline"
        >
          <template #actions>
            <v-btn variant="tonal" color="primary" :to="{ name: 'SpacesDetailPublishTask', params: { spaceId } }">
              {{ t('spaces.course.assignments.publish') }}
            </v-btn>
          </template>
        </v-empty-state>

        <v-table v-else density="comfortable">
          <thead>
            <tr>
              <th>{{ t('spaces.course.assignments.student') }}</th>
              <th>{{ t('spaces.course.assignments.assignment') }}</th>
              <th>{{ t('spaces.course.assignments.submittedAt') }}</th>
              <th>{{ t('spaces.course.assignments.state') }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td>
                {{ nameOf(row) }}
                <v-chip size="small" variant="tonal" class="ml-2">v{{ row.version }}</v-chip>
              </td>
              <td>{{ row.taskTitle }}</td>
              <td>{{ dayjs(row.createdAt).format('YYYY-MM-DD HH:mm') }}</td>
              <td>
                <v-chip size="small" variant="tonal" :color="stateColor(row)">
                  {{ stateText(row) }}
                </v-chip>
              </td>
              <td class="text-right">
                <v-btn size="small" variant="tonal" color="primary" @click="open(row)">
                  {{
                    row.review?.reviewed ? t('spaces.course.assignments.open') : t('spaces.course.assignments.grade')
                  }}
                </v-btn>
              </td>
            </tr>
          </tbody>
        </v-table>
      </template>
    </v-sheet>

    <!-- 看与打分复用既有的提交 / 评审组件。 -->
    <v-dialog v-model="dialog" max-width="880" scrollable>
      <v-card v-if="current" flat>
        <v-card-title class="d-flex align-center ga-2">
          <span>{{ t('spaces.course.assignments.dialogTitle') }}</span>
          <v-chip size="small" variant="tonal">{{ current.taskTitle }}</v-chip>
          <div class="flex-grow-1"></div>
          <v-btn icon="mdi-close" variant="text" @click="close"></v-btn>
        </v-card-title>
        <v-card-text>
          <TaskSubmissionHistory
            :task-id="current.taskId"
            :participant-id="current.participantId"
            :reviewable="true"
            :is-dialog="true"
          />
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { SpaceSubmissionRow, SpaceSubmissionSummary } from '@/network/api/spaces/types'
import type { SpaceMember } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { storeToRefs } from 'pinia'

import TaskSubmissionHistory from '@/components/tasks/TaskSubmissionHistory.vue'
import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { currentSpace: space } = storeToRefs(useSpaceStore())

const spaceId = String(route.params.spaceId)
const scope = ref<'pending' | 'all'>('pending')
const loading = ref(true)
const dialog = ref(false)
const current = ref<SpaceSubmissionRow | null>(null)
const rows = ref<SpaceSubmissionRow[]>([])
const summary = ref<SpaceSubmissionSummary | null>(null)
const members = ref<SpaceMember[]>([])

const isPending = computed(() => space.value?.reviewStatus === 'PENDING')

const stats = computed(() => {
  const s = summary.value
  if (!s) return []
  return [
    {
      key: 'participants',
      label: 'spaces.course.assignments.statParticipants',
      value: s.participants,
    },
    {
      key: 'submissions',
      label: 'spaces.course.assignments.statSubmitted',
      value: s.submissions,
    },
    {
      key: 'pending',
      label: 'spaces.course.assignments.statPending',
      value: s.pendingReview,
    },
    { key: 'missing', label: 'spaces.course.assignments.statMissing', value: s.missing },
  ]
})

const byUserId = computed(() => {
  const map = new Map<number, string>()
  for (const member of members.value) {
    const id = member.user?.id ?? member.userId
    const name = member.user?.nickname || member.user?.username
    if (id && name) map.set(id, name)
  }
  return map
})

// 提交自己带的是 id（服务端的 DTO 刻意不引用户域），名字在这一屏用名册补上；
// 名册里没有（例如报名的是一支小队）就退回 id，不假装知道是谁。
const nameOf = (row: SpaceSubmissionRow) => byUserId.value.get(row.submitter.id) ?? `#${row.submitter.id}`

const stateText = (row: SpaceSubmissionRow) => {
  if (!row.review?.reviewed) return t('spaces.course.assignments.statePending')
  return row.review.detail.accepted
    ? t('spaces.course.assignments.stateAccepted')
    : t('spaces.course.assignments.stateRejected')
}

const stateColor = (row: SpaceSubmissionRow) => {
  if (!row.review?.reviewed) return 'warning'
  return row.review.detail.accepted ? 'success' : 'error'
}

const reload = async () => {
  const id = Number(spaceId)
  loading.value = true
  if (!Number.isFinite(id) || id <= 0) {
    loading.value = false
    return
  }
  const [queue, memberList] = await Promise.allSettled([
    SpacesApi.getSubmissionQueue(id, {
      reviewed: scope.value === 'pending' ? false : undefined,
      pageSize: 50,
    }),
    SpacesApi.listMembers(id),
  ])
  if (queue.status === 'fulfilled') {
    rows.value = queue.value.data.submissions
    summary.value = queue.value.data.summary
  }
  if (memberList.status === 'fulfilled') members.value = memberList.value.data.members
  loading.value = false
}

const open = (row: SpaceSubmissionRow) => {
  current.value = row
  dialog.value = true
}

const close = () => {
  dialog.value = false
  current.value = null
  // 打完分 / 撤了评审之后队列要跟着变 —— 这一屏的队列就是这份列表的过滤。
  reload()
}

onMounted(reload)
</script>

<style scoped lang="scss">
.stat {
  border: 1px solid var(--line);
}
</style>
