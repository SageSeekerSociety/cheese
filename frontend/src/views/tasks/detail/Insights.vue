<template>
  <InsightsView
    :task="taskData"
    :roster="roster"
    :review-by-participant="reviewByParticipant"
    :can-manage="Boolean(isCreator || isAdmin)"
    :loading="loading"
  />
</template>

<script setup lang="ts">
// 题目详情的「数据」页签：出题人和管理员看自己这道题的情况，画面在 `InsightsView.vue`。
//
// 和整板看板的分工：整板看板回答「这个空间怎么样」（管理员的问题），这一页只回答
// 「**我这道题**怎么样」（出题人的问题）。所以这里没有跨题排行、没有分类分布。逐人的
// 名单在「领取者」页签，这里不再列一份。
//
// 数据来自两个真接口，没有一个数字是本地编的：
// - `GET /tasks/{id}/participants` —— 领取名单（含每人加入时刻，走势图就从它算）
// - `GET /spaces/{id}/submissions?taskId=…` —— 这道题的所有提交（含判没判、判过没过）
import type { Task, TaskMembership, TaskSubmissionReview } from '@/types'

import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import InsightsView from './InsightsView.vue'

import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'

const props = defineProps<{
  taskData: Task | null
  isCreator?: boolean
  isAdmin?: boolean
}>()

const route = useRoute()

const roster = ref<TaskMembership[]>([])
const reviewByParticipant = ref(new Map<number, TaskSubmissionReview | undefined>())
const loading = ref(true)

async function load() {
  const taskId = props.taskData?.id
  if (!taskId) return
  loading.value = true
  try {
    const [rosterRes, subsRes] = await Promise.all([
      TasksApi.getParticipants(taskId),
      SpacesApi.getSubmissionQueue(Number(route.params.spaceId), { taskId, pageSize: 200 }),
    ])
    roster.value = rosterRes.data.participants ?? []

    // 同一人可能有多版提交：按 version 取最大的那一版 —— 看的永远是「现在这一版判没判」。
    const byVersion = new Map<number, { version: number; review?: TaskSubmissionReview }>()
    for (const row of subsRes.data.submissions ?? []) {
      const prev = byVersion.get(row.participantId)
      if (!prev || row.version > prev.version) {
        byVersion.set(row.participantId, { version: row.version, review: row.review })
      }
    }
    reviewByParticipant.value = new Map([...byVersion.entries()].map(([pid, v]) => [pid, v.review]))
  } catch {
    roster.value = []
    reviewByParticipant.value = new Map()
  } finally {
    loading.value = false
  }
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
