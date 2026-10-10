<template>
  <RosterView
    :task-data="taskData"
    :participants="participants"
    :latest-by-participant="latestByParticipant"
    :loading="loading"
    :denied="denied"
    :failed="failed"
    :failure-reason="failureReason"
    :busy-id="busyId"
    @approve="approve"
    @reject="reject"
    @deadline="setDeadline"
    @retry="load"
    @review="events.emit('review-participant', $event)"
  />
</template>

<script setup lang="ts">
// 「领取者」页签：取名单与提交、批领取申请、设截止；画面在 `RosterView.vue`。
// 逐版评审那张对话框挂在题目详情那一层（它自己取数），这里只在总线上说要看谁的。
// 这里批了、拒了、改了截止，或者那张对话框里判了一版，都在总线上发 `participation-changed`：
// 这里听见了重读名单，题目页听见了重读我自己那一份（出题人也可能领了自己的题）。
import type { Task, TaskMembership } from '@/types'
import type { Latest } from './RosterView.vue'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import RosterView from './RosterView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'
import { useEvents } from '@/views/tasks/events'

const props = defineProps<{
  taskData: Task | null
}>()

const { t } = useI18n()
const route = useRoute()
const events = useEvents()

const participants = ref<TaskMembership[]>([])
const latestByParticipant = ref(new Map<number, Latest>())
const loading = ref(true)
/** 名单没读出来时的那个错。403 是「不给你看」，其余是「这次没读到」——在下面分开。 */
const loadError = ref<unknown>(null)
const busyId = ref<number | null>(null)

// 是「不给你看」还是「这次没读到」，在这里判：画面只拿布尔值和那句话，不认状态码。
const denied = computed(() => isForbidden(loadError.value))
const failed = computed(() => loadError.value !== null && participants.value.length === 0)
const failureReason = computed(() => loadFailureReason(loadError.value))

async function load() {
  const task = props.taskData
  if (!task) return
  // 上一次的失败不许留到这一次：重新问一次，屏幕上先干净。
  loadError.value = null
  try {
    const [rosterRes, subsRes] = await Promise.all([
      TasksApi.getParticipants(task.id, { queryRealNameInfo: true, queryTeamInfo: true }),
      SpacesApi.getSubmissionQueue(Number(route.params.spaceId), { taskId: task.id, pageSize: 200 }),
    ])
    participants.value = rosterRes.data.participants ?? []
    // 同一人可能有多版：按 `version` 取最大的那一版 —— 要回答的永远是「现在这一版判没判」。
    const latest = new Map<number, Latest>()
    for (const s of subsRes.data.submissions ?? []) {
      const prev = latest.get(s.participantId)
      if (!prev || s.version > prev.version) {
        latest.set(s.participantId, {
          submissionId: s.id,
          version: s.version,
          createdAt: s.createdAt,
          review: s.review ?? undefined,
        })
      }
    }
    latestByParticipant.value = latest
  } catch (error) {
    // 原来这里不看错是什么，一律画「只有出题人和管理员能看领取者」：接口 500 的时候，
    // 出题人会去查自己的权限 —— 方向就是错的。是「不给你看」还是「这次没读到」，
    // 上面那三个从这个错算出来的值分得开。
    loadError.value = error
  } finally {
    loading.value = false
  }
}

function nameOf(id: number): string {
  const m = participants.value.find((p) => p.id === id)
  return m?.member?.name ?? ''
}

/** 批准时顺手给这个人定截止：领取后 N 天（题目的「提交期限」），没设就 7 天。
 *  发题表单存的是天数，老数据里存的是毫秒，两种都换成天。 */
async function approve(id: number) {
  const task = props.taskData
  if (!task) return
  busyId.value = id
  const raw = task.defaultDeadline ?? 0
  const days = raw >= 86_400_000 ? Math.round(raw / 86_400_000) : raw
  try {
    await TasksApi.updateParticipant(task.id, id, {
      approved: 'APPROVED',
      deadline: dayjs()
        .add(days || 7, 'day')
        .valueOf(),
    })
    toast.success(t('tasks.roster.approved', { name: nameOf(id) }))
    events.emit('participation-changed')
  } catch {
    toast.error(t('tasks.roster.approveFailed'))
  } finally {
    busyId.value = null
  }
}

async function reject(id: number, reason: string) {
  const task = props.taskData
  if (!task) return
  try {
    await TasksApi.updateParticipant(task.id, id, { approved: 'DISAPPROVED', rejectReason: reason || undefined })
    toast.success(t('tasks.roster.rejectDone', { name: nameOf(id) }))
    events.emit('participation-changed')
  } catch {
    toast.error(t('tasks.roster.rejectFailed'))
  }
}

async function setDeadline(id: number, at: number) {
  const task = props.taskData
  if (!task) return
  try {
    await TasksApi.updateParticipant(task.id, id, { deadline: at })
    toast.success(t('tasks.roster.deadlineSaved'))
    events.emit('participation-changed')
  } catch {
    toast.error(t('tasks.roster.deadlineFailed'))
  }
}

onMounted(() => events.on('participation-changed', load))
watch(() => props.taskData?.id, load, { immediate: true })
</script>
