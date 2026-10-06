<template>
  <SubmitView
    v-model:progress-dialog="progressDialog"
    :task-data="taskData"
    :participation-info="participationInfo"
    :reached-submission-limit="reachedSubmissionLimit"
    :submitting="submitting"
    :upload-progress="uploadProgress"
    :current-file-name="currentFileName"
    :upload-speed="uploadSpeed"
    :time-remaining="timeRemaining"
    @submit="submitTask"
  />
</template>

<script setup lang="ts">
// 容器：查这个身份交过没有、传文件、建提交、跳转都在这儿；画面交给 SubmitView。
import type { TaskParticipationInfo } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { throttle } from 'lodash-es'

import SubmitView from './SubmitView.vue'

import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { AttachmentsApi } from '@/network/api/attachments'
import { TasksApi } from '@/network/api/tasks'

const { t } = useI18n()

const routeNames = TASK_ROUTE_NAMES

const props = defineProps<{
  taskData: Task | null
  participationInfo?: TaskParticipationInfo
}>()

const router = useRouter()
const submitting = ref(false)
const progressDialog = ref(false)
const uploadProgress = ref(0)
const currentFileName = ref('')
const uploadSpeed = ref('0 KB/s')
const timeRemaining = ref('')

// 是否已达到提交次数上限（对于不可重复提交的任务）：这个身份已经交过一版。
// 可重复提交的题不查；查不到（网络、权限）就当没交过 —— 真交第二次时服务端会拒。
const alreadySubmitted = ref(false)
const reachedSubmissionLimit = computed(() =>
  Boolean(props.taskData && !props.taskData.resubmittable && alreadySubmitted.value)
)

// 当前选中的身份由 View 持有，这里镜像一份它的 id 去查提交记录。
const selectedIdentityId = ref<number | null>(null)

watch(
  () => props.participationInfo,
  (newVal) => {
    if (newVal?.identities && newVal.identities.length > 0) {
      const validIdentity = newVal.identities.find((id) => id.approved === 'APPROVED' && id.canSubmit)
      selectedIdentityId.value = validIdentity?.id ?? newVal.identities[0].id
    }
  },
  { immediate: true }
)

watch(
  () => [props.taskData?.id, props.taskData?.resubmittable, selectedIdentityId.value] as const,
  async ([taskId, resubmittable, identityId]) => {
    alreadySubmitted.value = false
    if (!taskId || resubmittable || !identityId) return
    try {
      const { data } = await TasksApi.listSubmissions(taskId, identityId, {
        pageSize: 1,
        sort_by: 'createdAt',
        sort_order: 'desc',
      })
      alreadySubmitted.value = data.submissions.length > 0
    } catch {
      alreadySubmitted.value = false
    }
  },
  { immediate: true }
)

// 节流的进度更新函数
const updateProgress = throttle((progressEvent: any) => {
  const { loaded, total } = progressEvent
  uploadProgress.value = Math.round((loaded / total) * 100)

  // 计算上传速度
  const speed = progressEvent.rate || 0
  if (speed < 1024) {
    uploadSpeed.value = `${speed.toFixed(1)} B/s`
  } else if (speed < 1024 * 1024) {
    uploadSpeed.value = `${(speed / 1024).toFixed(1)} KB/s`
  } else {
    uploadSpeed.value = `${(speed / (1024 * 1024)).toFixed(1)} MB/s`
  }

  // 计算剩余时间
  const remaining = (total - loaded) / speed
  if (remaining < 60) {
    timeRemaining.value = t('tasks.submit.seconds', { n: Math.ceil(remaining) })
  } else if (remaining < 3600) {
    timeRemaining.value = t('tasks.submit.minutes', { n: Math.ceil(remaining / 60) })
  } else {
    timeRemaining.value = t('tasks.submit.hoursMinutes', {
      h: Math.floor(remaining / 3600),
      m: Math.ceil((remaining % 3600) / 60),
    })
  }
}, 200)

// 提交任务
const submitTask = async (payload: {
  content: { contentText?: string; contentAttachment?: File }[]
  identityId: number
}) => {
  if (!props.taskData) return

  const submissionSchema = props.taskData.submissionSchema || []

  // 检查表单是否填写完整
  const isFormValid = payload.content.every((item, index) => {
    const schema = submissionSchema[index]
    if (schema.type === 'TEXT') {
      return !!item.contentText
    } else if (schema.type === 'FILE') {
      return !!item.contentAttachment
    }
    return true
  })

  if (!isFormValid) {
    toast.error(t('tasks.submit.fillRequired'))
    return
  }

  submitting.value = true
  progressDialog.value = true
  uploadProgress.value = 0

  try {
    // 处理文件上传
    const finalSubmissionContent = payload.content.map((item) => {
      if (item.contentAttachment) {
        return {} as { text?: string; attachmentId?: number }
      }
      return {
        text: item.contentText,
      } as { text?: string; attachmentId?: number }
    })

    // 上传文件
    for (const [index, entry] of payload.content.entries()) {
      if (entry.contentAttachment) {
        try {
          currentFileName.value = entry.contentAttachment.name
          uploadProgress.value = 0
          uploadSpeed.value = '0 KB/s'
          timeRemaining.value = t('tasks.submit.calculating')

          const { data } = await AttachmentsApi.upload(
            {
              type: 'file',
              file: entry.contentAttachment,
            },
            updateProgress
          )

          finalSubmissionContent[index].attachmentId = data.id
        } catch (error) {
          toast.error(
            t('tasks.submit.uploadFailed', {
              error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
            })
          )
          progressDialog.value = false
          submitting.value = false
          return
        }
      }
    }

    // 使用参与者ID而不是成员ID进行提交
    await TasksApi.createSubmission(props.taskData.id, payload.identityId, finalSubmissionContent)
    toast.success(t('tasks.submit.submitted'))

    // 跳转到提交记录页面
    router.push({
      name: routeNames.submissions,
      params: { spaceId: props.taskData.space?.id, taskId: props.taskData.id },
    })
  } catch (error) {
    toast.error(
      t('tasks.submit.submitFailed', { error: error instanceof Error ? error.message : t('tasks.submit.unknownError') })
    )
  } finally {
    progressDialog.value = false
    submitting.value = false
  }
}
</script>
