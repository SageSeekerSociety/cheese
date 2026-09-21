<template>
  <div>
    <v-card flat rounded="lg" class="mb-6">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-icon color="primary" size="28">mdi-account-group</v-icon>
          </div>
        </template>
        <v-card-title class="text-h6 ps-0">{{ t('tasks.detail.participants.title') }}</v-card-title>
        <template #append>
          <div class="d-flex align-center gap-2">
            <v-btn-group density="comfortable">
              <v-btn
                :color="filterStatus === 'all' ? 'primary' : undefined"
                variant="text"
                @click="filterStatus = 'all'"
              >
                {{ t('tasks.detail.participants.all') }}
                <v-chip class="ml-2" size="x-small" color="primary" variant="flat">{{ participants.length }}</v-chip>
              </v-btn>
              <v-btn
                :color="filterStatus === 'pending' ? 'primary' : undefined"
                variant="text"
                @click="filterStatus = 'pending'"
              >
                {{ t('tasks.detail.participants.pending') }}
                <v-chip class="ml-2" size="x-small" color="warning" variant="flat">{{ pendingCount }}</v-chip>
              </v-btn>
              <v-btn
                :color="filterStatus === 'approved' ? 'primary' : undefined"
                variant="text"
                @click="filterStatus = 'approved'"
              >
                {{ t('tasks.detail.participants.approved') }}
                <v-chip class="ml-2" size="x-small" color="success" variant="flat">{{ approvedCount }}</v-chip>
              </v-btn>
              <v-btn
                :color="filterStatus === 'rejected' ? 'primary' : undefined"
                variant="text"
                @click="filterStatus = 'rejected'"
              >
                {{ t('tasks.detail.participants.rejected') }}
                <v-chip class="ml-2" size="x-small" color="error" variant="flat">{{ rejectedCount }}</v-chip>
              </v-btn>
            </v-btn-group>
          </div>
        </template>
      </v-card-item>

      <v-divider class="mb-2"></v-divider>

      <v-card-text v-if="loading" class="d-flex justify-center py-6">
        <v-progress-circular indeterminate color="primary"></v-progress-circular>
      </v-card-text>

      <v-card-text v-else-if="filteredParticipants.length === 0" class="py-12">
        <v-empty-state
          :title="
            participants.length === 0
              ? t('tasks.detail.participants.noParticipants')
              : t('tasks.detail.participants.noMatches')
          "
          :text="
            participants.length === 0
              ? t('tasks.detail.participants.noParticipantsHint')
              : t('tasks.detail.participants.noMatchesHint')
          "
          icon="mdi-account-group"
        ></v-empty-state>
      </v-card-text>

      <template v-else>
        <v-list class="participants-list">
          <v-list-item
            v-for="participant in filteredParticipants"
            :key="participant.id"
            :value="participant.id"
            class="participant-item mb-2 mx-2 rounded-lg"
            :class="{
              'border-pending': participant.approved === 'NONE',
              'border-rejected': participant.approved === 'DISAPPROVED',
            }"
          >
            <template #prepend>
              <v-avatar
                :color="
                  taskData?.submitterType === 'TEAM'
                    ? 'success'
                    : participant.realNameInfo?.realName
                      ? 'primary'
                      : 'primary-lighten-3'
                "
                size="48"
              >
                <v-img
                  v-if="participant.member.avatarId && !taskData?.requireRealName"
                  :src="getAvatarUrl(participant.member.avatarId)"
                  cover
                ></v-img>
                <span v-else>{{ getParticipantDisplayName(participant).slice(0, 1) }}</span>
              </v-avatar>
            </template>

            <v-list-item-title>
              <div class="d-flex align-center flex-wrap gap-2">
                <span class="font-weight-medium">{{ getParticipantDisplayName(participant) }}</span>
                <v-chip
                  v-if="taskData?.submitterType === 'TEAM'"
                  variant="tonal"
                  color="success"
                  size="small"
                  class="my-1"
                >
                  {{ t('tasks.detail.participants.team') }}
                </v-chip>
                <v-chip
                  v-if="participant.approved === 'NONE'"
                  variant="tonal"
                  color="warning"
                  size="small"
                  class="my-1"
                >
                  {{ t('tasks.detail.participants.pending') }}
                </v-chip>
                <v-chip
                  v-else-if="participant.approved === 'DISAPPROVED'"
                  variant="tonal"
                  color="error"
                  size="small"
                  class="my-1"
                >
                  {{ t('tasks.detail.participants.rejected') }}
                </v-chip>
                <v-chip
                  v-else-if="participant.approved === 'APPROVED'"
                  variant="tonal"
                  color="success"
                  size="small"
                  class="my-1"
                >
                  {{ t('tasks.detail.participants.approved') }}
                </v-chip>
              </div>
            </v-list-item-title>

            <v-list-item-subtitle>
              <template v-if="taskData?.submitterType === 'USER'">
                <div v-if="participant.realNameInfo?.realName" class="d-flex flex-wrap gap-2 mt-1">
                  <span
                    v-if="participant.realNameInfo.grade || participant.realNameInfo.className"
                    class="d-inline-flex align-center"
                  >
                    <v-icon size="16" class="me-1">mdi-school</v-icon>
                    {{
                      t('tasks.detail.participants.gradeClass', {
                        grade: participant.realNameInfo.grade,
                        className: participant.realNameInfo.className,
                      })
                    }}
                  </span>
                </div>
                <div class="d-flex flex-wrap gap-2 mt-1">
                  <span v-if="participant.phone" class="d-inline-flex align-center">
                    <v-icon size="16" class="me-1">mdi-phone</v-icon>
                    {{ participant.phone }}
                  </span>
                  <span v-if="participant.email" class="d-inline-flex align-center">
                    <v-icon size="16" class="me-1">mdi-email</v-icon>
                    {{ participant.email }}
                  </span>
                </div>
                <div v-if="participant.applyReason" class="mt-1">
                  <v-icon size="16" class="me-1">mdi-text-box</v-icon>
                  {{ t('tasks.detail.participants.applyReason', { reason: participant.applyReason }) }}
                </div>
                <div v-if="participant.personalAdvantage" class="mt-1">
                  <v-icon size="16" class="me-1">mdi-star</v-icon>
                  {{ t('tasks.detail.participants.personalAdvantage', { advantage: participant.personalAdvantage }) }}
                </div>
              </template>
              <template v-if="taskData?.submitterType === 'TEAM'">
                <div v-if="participant.teamMembers?.length" class="d-flex flex-wrap gap-1 mt-1">
                  <span class="d-inline-flex align-center">
                    <v-icon size="16" class="me-1">mdi-account-multiple</v-icon>
                    {{ t('tasks.detail.participants.teamMembers') }}
                  </span>
                  <v-chip
                    v-for="(member, index) in participant.teamMembers"
                    :key="index"
                    size="x-small"
                    class="ms-1"
                    :color="member.isLeader ? 'primary' : ''"
                    :variant="member.isLeader ? 'flat' : 'outlined'"
                  >
                    <span v-if="taskData?.requireRealName && member.realNameInfo?.realName">
                      {{ member.realNameInfo.realName }}
                      <span v-if="member.realNameInfo.studentId"> ({{ member.realNameInfo.studentId }}) </span>
                    </span>
                    <span v-else>{{ member.name }}</span>
                    <v-icon v-if="member.isLeader" size="12" class="ms-1">mdi-crown</v-icon>
                  </v-chip>
                </div>
                <div class="d-flex flex-wrap gap-2 mt-1">
                  <span v-if="participant.phone" class="d-inline-flex align-center">
                    <v-icon size="16" class="me-1">mdi-phone</v-icon>
                    {{ participant.phone }}
                  </span>
                  <span v-if="participant.email" class="d-inline-flex align-center">
                    <v-icon size="16" class="me-1">mdi-email</v-icon>
                    {{ participant.email }}
                  </span>
                </div>
                <div v-if="participant.applyReason" class="mt-1">
                  <v-icon size="16" class="me-1">mdi-text-box</v-icon>
                  {{ t('tasks.detail.participants.applyReason', { reason: participant.applyReason }) }}
                </div>
                <div v-if="participant.personalAdvantage" class="mt-1">
                  <v-icon size="16" class="me-1">mdi-star</v-icon>
                  {{ t('tasks.detail.participants.teamAdvantage', { advantage: participant.personalAdvantage }) }}
                </div>
              </template>
              <template v-if="participant.deadline">
                <div class="d-flex align-center text-primary mt-1">
                  <v-icon size="16" class="me-1">mdi-clock</v-icon>
                  {{ t('tasks.detail.participants.deadline', { date: formatDate(participant.deadline) }) }}
                </div>
              </template>
            </v-list-item-subtitle>

            <template #append>
              <div class="d-flex flex-column flex-sm-row align-end align-sm-center gap-2">
                <template v-if="participant.approved === 'APPROVED'">
                  <v-btn
                    prepend-icon="mdi-calendar-clock"
                    variant="outlined"
                    color="primary"
                    size="small"
                    @click="showDeadlineDialog(participant)"
                  >
                    {{ t('tasks.detail.participants.setDeadline') }}
                  </v-btn>
                  <v-btn
                    prepend-icon="mdi-eye"
                    variant="tonal"
                    color="primary"
                    size="small"
                    @click="showSubmissions(participant)"
                  >
                    {{ t('tasks.detail.participants.viewSubmissions') }}
                  </v-btn>
                </template>
                <template v-else-if="participant.approved === 'NONE'">
                  <div class="d-flex gap-2">
                    <v-btn
                      prepend-icon="mdi-check"
                      color="success"
                      variant="tonal"
                      size="small"
                      @click="approveParticipant(participant)"
                    >
                      {{ t('tasks.detail.participants.approve') }}
                    </v-btn>
                    <v-btn
                      prepend-icon="mdi-close"
                      color="error"
                      variant="tonal"
                      size="small"
                      @click="rejectParticipant(participant)"
                    >
                      {{ t('tasks.detail.participants.reject') }}
                    </v-btn>
                  </div>
                </template>
              </div>
            </template>
          </v-list-item>
        </v-list>
      </template>
    </v-card>

    <!-- 设置截止时间对话框 -->
    <v-dialog v-model="deadlineDialog" max-width="500" class="deadline-dialog">
      <v-card rounded="lg">
        <v-card-title class="text-h6 pa-4">
          <v-icon left color="primary" class="mr-2">mdi-calendar-clock</v-icon>
          {{ t('tasks.detail.participants.deadlineDialogTitle') }}
        </v-card-title>
        <v-card-text class="pa-4 pt-0">
          <i18n-t keypath="tasks.detail.participants.deadlineDialogBody" scope="global" tag="p" class="mb-4">
            <template #name>
              <span class="font-weight-medium">{{
                selectedParticipant ? getParticipantDisplayName(selectedParticipant) : ''
              }}</span>
            </template>
          </i18n-t>
          <v-form ref="deadlineFormRef">
            <v-date-picker
              v-model="newDeadlineDate"
              class="mb-4"
              elevation="0"
              :min="tomorrow"
              color="primary"
            ></v-date-picker>
            <v-text-field
              v-model="newDeadlineTime"
              :label="t('tasks.detail.participants.timeLabel')"
              variant="outlined"
              type="time"
              class="mb-2"
            ></v-text-field>
          </v-form>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer></v-spacer>
          <v-btn variant="outlined" @click="deadlineDialog = false">{{ t('tasks.detail.participants.cancel') }}</v-btn>
          <v-btn color="primary" @click="setDeadline">{{ t('tasks.detail.participants.confirm') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 提交历史对话框 -->
    <v-dialog v-model="submissionsDialog" fullscreen scrollable>
      <v-card>
        <v-toolbar color="primary" dark class="elevation-0">
          <v-btn icon @click="submissionsDialog = false">
            <v-icon>mdi-close</v-icon>
          </v-btn>
          <v-toolbar-title>
            <i18n-t keypath="tasks.detail.participants.submissionsTitle" scope="global" tag="span">
              <template #name>{{ selectedParticipant ? getParticipantDisplayName(selectedParticipant) : '' }}</template>
            </i18n-t>
          </v-toolbar-title>
          <v-spacer></v-spacer>
        </v-toolbar>
        <v-card-text class="pa-6">
          <TaskSubmissionHistory
            v-if="selectedParticipant && taskData"
            :task-id="taskData.id"
            :participant-id="selectedParticipant.id"
            :reviewable="true"
            :is-dialog="true"
            :outlined="true"
            :highlight-latest="true"
            :title="t('tasks.detail.participants.latestSubmission')"
            :history-title="t('tasks.detail.participants.submissionHistory')"
          />
        </v-card-text>
      </v-card>
    </v-dialog>

    <!-- 驳回原因对话框 -->
    <v-dialog v-model="rejectDialog" max-width="500" class="reject-dialog">
      <v-card rounded="lg">
        <v-card-title class="text-h6 pa-4">
          <v-icon left color="error" class="mr-2">mdi-close-circle</v-icon>
          {{ t('tasks.detail.participants.rejectDialogTitle') }}
        </v-card-title>
        <v-card-text class="pa-4 pt-0">
          <i18n-t keypath="tasks.detail.participants.rejectDialogBody" scope="global" tag="p" class="mb-4">
            <template #name>
              <span class="font-weight-medium">{{
                selectedParticipant ? getParticipantDisplayName(selectedParticipant) : ''
              }}</span>
            </template>
          </i18n-t>
          <v-textarea
            v-model="rejectReason"
            autocomplete="off"
            :label="t('tasks.detail.participants.rejectReasonLabel')"
            :placeholder="t('tasks.detail.participants.rejectReasonPlaceholder')"
            variant="outlined"
            rows="3"
            counter="200"
            max-length="200"
            class="mb-2"
          ></v-textarea>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer></v-spacer>
          <v-btn variant="outlined" @click="rejectDialog = false">{{ t('tasks.detail.participants.cancel') }}</v-btn>
          <v-btn color="error" @click="confirmReject">{{ t('tasks.detail.participants.confirmReject') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { Task, TaskMembership } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { getAvatarUrl } from '@/utils/materials'

import { TasksApi } from '@/network/api/tasks'

const TaskSubmissionHistory = defineAsyncComponent(() => import('@/components/tasks/TaskSubmissionHistory.vue'))

const { t } = useI18n()

const props = defineProps<{
  taskData: Task | null
}>()

// 数据状态
const participants = ref<TaskMembership[]>([])
const loading = ref(true)
const searchQuery = ref('')
const filterStatus = ref('all')
const currentPage = ref(1)

// 对话框状态
const deadlineDialog = ref(false)
const submissionsDialog = ref(false)
const rejectDialog = ref(false)
const selectedParticipant = ref<TaskMembership | null>(null)
const newDeadlineDate = ref<Date>()
const newDeadlineTime = ref('23:59')
const rejectReason = ref('')

// 计算属性
const tomorrow = computed(() => {
  return dayjs().add(1, 'day').format('YYYY-MM-DD')
})

const pendingCount = computed(() => {
  return participants.value.filter((p) => p.approved === 'NONE').length
})

const approvedCount = computed(() => {
  return participants.value.filter((p) => p.approved === 'APPROVED').length
})

const rejectedCount = computed(() => {
  return participants.value.filter((p) => p.approved === 'DISAPPROVED').length
})

const filteredParticipants = computed(() => {
  let result = participants.value

  // 按状态筛选
  if (filterStatus.value === 'pending') {
    result = result.filter((p) => p.approved === 'NONE')
  } else if (filterStatus.value === 'approved') {
    result = result.filter((p) => p.approved === 'APPROVED')
  } else if (filterStatus.value === 'rejected') {
    result = result.filter((p) => p.approved === 'DISAPPROVED')
  }

  return result
})

// 方法
const formatDate = (date: string | Date | number) => {
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

const getParticipantDisplayName = (participant: TaskMembership) => {
  // 对于团队类型的参与者，显示团队名称
  if (props.taskData?.submitterType === 'TEAM') {
    return participant.member?.name || t('tasks.detail.participants.unknownTeam')
  }

  // 对于个人类型的参与者
  if (props.taskData?.requireRealName && participant.realNameInfo?.realName) {
    return `${participant.realNameInfo.realName}${participant.realNameInfo.studentId ? ` (${participant.realNameInfo.studentId})` : ''}`
  }

  return participant.member?.name || t('tasks.detail.participants.unknownUser')
}

const fetchParticipants = async () => {
  if (!props.taskData) return

  loading.value = true
  try {
    const { data } = await TasksApi.getParticipants(props.taskData.id, {
      queryRealNameInfo: true,
      queryTeamInfo: true,
    })

    participants.value = data.participants
  } catch (error) {
    toast.error(t('tasks.detail.participants.loadFailed'))
  } finally {
    loading.value = false
  }
}

const showDeadlineDialog = (participant: TaskMembership) => {
  selectedParticipant.value = participant
  const defaultDate = participant.deadline ? dayjs(participant.deadline).toDate() : new Date(tomorrow.value)
  const defaultTime = participant.deadline ? dayjs(participant.deadline).format('HH:mm') : '23:59'

  newDeadlineDate.value = defaultDate
  newDeadlineTime.value = defaultTime
  deadlineDialog.value = true
}

const setDeadline = async () => {
  if (!selectedParticipant.value || !props.taskData) return

  // 组合日期和时间
  const deadline = dayjs(`${newDeadlineDate.value} ${newDeadlineTime.value}`).valueOf()

  try {
    await TasksApi.updateParticipant(props.taskData.id, selectedParticipant.value.id, { deadline })
    toast.success(t('tasks.detail.participants.deadlineSetSuccess'))
    deadlineDialog.value = false
    await fetchParticipants()
  } catch (error) {
    toast.error(t('tasks.detail.participants.deadlineSetFailed'))
  }
}

const showSubmissions = (participant: TaskMembership) => {
  selectedParticipant.value = participant
  submissionsDialog.value = true
}

const approveParticipant = async (participant: TaskMembership) => {
  if (!props.taskData) return

  // 设置默认截止时间（当前时间 + 默认天数）
  const defaultDeadlineDays = props.taskData.defaultDeadline || 7
  const deadline = dayjs().add(defaultDeadlineDays, 'day').valueOf()

  try {
    await TasksApi.updateParticipant(props.taskData.id, participant.id, { approved: 'APPROVED', deadline })
    toast.success(t('tasks.detail.participants.approvedSuccess'))
    await fetchParticipants()
  } catch (error) {
    toast.error(t('tasks.detail.participants.operationFailed', { error }))
  }
}

const rejectParticipant = (participant: TaskMembership) => {
  selectedParticipant.value = participant
  rejectReason.value = ''
  rejectDialog.value = true
}

const confirmReject = async () => {
  if (!selectedParticipant.value || !props.taskData) return

  try {
    await TasksApi.updateParticipant(props.taskData.id, selectedParticipant.value.id, {
      approved: 'DISAPPROVED',
      rejectReason: rejectReason.value || undefined,
    })
    toast.success(t('tasks.detail.participants.rejectedSuccess'))
    rejectDialog.value = false
    await fetchParticipants()
  } catch (error) {
    toast.error(t('tasks.detail.participants.operationFailed', { error }))
  }
}

// 监听器
watch(
  () => props.taskData,
  (newVal) => {
    if (newVal) {
      fetchParticipants()
    }
  },
  { immediate: true }
)

watch([filterStatus, searchQuery], () => {
  currentPage.value = 1
})
</script>

<style scoped>
.participants-list {
  background-color: transparent;
}

.participant-item {
  background-color: var(--v-theme-surface);
  transition: all 0.3s ease;
  border: 1px solid transparent;
}

.participant-item:hover {
  background-color: var(--v-theme-surface-light);
  transform: translateY(-2px);
  box-shadow: var(--shadow-1);
}

.border-pending {
  border-color: rgba(var(--v-theme-warning), 0.3);
}

.border-rejected {
  border-color: rgba(var(--v-theme-error), 0.3);
}

.max-width-200 {
  max-width: 200px;
}

@media (max-width: 600px) {
  .max-width-200 {
    max-width: 100%;
  }
}
</style>
