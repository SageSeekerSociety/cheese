<template>
  <div>
    <v-card flat rounded="lg" class="mb-6">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-icon color="primary" size="28">mdi-upload</v-icon>
          </div>
        </template>
        <v-card-title class="text-h6 ps-0">{{ t('tasks.submit.formTitle') }}</v-card-title>
        <template #append>
          <CountdownTimer v-if="userDeadline" :deadline="userDeadline" :label="t('tasks.submit.timeLeft')" />
        </template>
      </v-card-item>

      <v-divider class="mx-4"></v-divider>

      <v-card-text class="py-6">
        <v-alert v-if="!taskData?.submittable" type="warning" class="mb-6" variant="tonal">
          {{ t('tasks.submit.notSubmittable') }}
        </v-alert>

        <v-alert v-else-if="!hasValidIdentity" type="warning" class="mb-6" variant="tonal">
          <template #title>{{ t('tasks.submit.noIdentityTitle') }}</template>
          <template #text>
            <p>{{ t('tasks.submit.noIdentityText') }}</p>
          </template>
        </v-alert>

        <v-alert v-else-if="reachedSubmissionLimit" type="warning" class="mb-6" variant="tonal">
          <template #title>{{ t('tasks.submit.limitTitle') }}</template>
          <template #text>
            <p>{{ t('tasks.submit.limitText') }}</p>
            <div class="mt-2">
              <BaseButton
                kind="secondary"
                :to="{ name: routeNames.submissions, params: { spaceId: taskData.space?.id, taskId: taskData.id } }"
                >{{ t('tasks.submit.viewMine') }}</BaseButton
              >
            </div>
          </template>
        </v-alert>

        <template v-else-if="submissionIdentities.length > 1">
          <!-- 如果有多个可提交身份，显示选择器 -->
          <v-card class="mb-6" variant="outlined">
            <v-card-text>
              <div class="text-subtitle-1 font-weight-medium mb-3">{{ t('tasks.submit.chooseIdentity') }}</div>
              <v-radio-group v-model="selectedIdentityId" class="mt-2">
                <v-radio
                  v-for="identity in submissionIdentities"
                  :key="identity.id"
                  :value="identity.id"
                  :label="
                    identity.type === 'TEAM'
                      ? t('tasks.submissions.teamIdentity', {
                          name: identity.teamName || t('tasks.submissions.unnamedTeam'),
                        })
                      : t('tasks.submit.individualIdentity')
                  "
                ></v-radio>
              </v-radio-group>
            </v-card-text>
          </v-card>
        </template>

        <v-form v-if="canSubmit" ref="formRef" @submit.prevent="onSubmit">
          <div class="form-container">
            <template v-for="(entry, index) in submissionSchema" :key="index">
              <div class="submission-entry pb-4 mb-4" :class="{ 'border-bottom': index < submissionSchema.length - 1 }">
                <div class="d-flex align-center mb-3">
                  <div class="me-auto">
                    <div class="text-subtitle-1 font-weight-medium">{{ entry.prompt }}</div>
                  </div>
                  <v-chip size="small" :color="getTypeColor(entry.type)" variant="flat">{{
                    getTypeText(entry.type)
                  }}</v-chip>
                </div>

                <v-fade-transition>
                  <div>
                    <template v-if="entry.type === 'TEXT'">
                      <v-textarea
                        v-model="submissionContent[index].contentText"
                        autocomplete="off"
                        :label="entry.prompt"
                        :placeholder="t('tasks.submit.enterPlaceholder', { prompt: entry.prompt })"
                        variant="outlined"
                        :rules="[(v) => !!v || t('tasks.submit.required')]"
                        hide-details="auto"
                        class="submission-input"
                      ></v-textarea>
                    </template>

                    <template v-else-if="entry.type === 'FILE'">
                      <v-file-input
                        v-model="submissionContent[index].contentAttachment"
                        :label="entry.prompt"
                        :placeholder="t('tasks.submit.uploadPlaceholder', { prompt: entry.prompt })"
                        variant="outlined"
                        :rules="[(v) => !!v || t('tasks.submit.required')]"
                        hide-details="auto"
                        prepend-icon=""
                        class="submission-input"
                      >
                        <template #prepend>
                          <v-icon color="primary" class="mr-2">mdi-paperclip</v-icon>
                        </template>
                        <template #selection="{ fileNames }">
                          <v-chip color="primary" variant="outlined" label class="mt-1">
                            <v-icon start>mdi-file</v-icon>
                            {{ fileNames[0] }}
                          </v-chip>
                        </template>
                      </v-file-input>
                    </template>
                  </div>
                </v-fade-transition>
              </div>
            </template>

            <div class="d-flex justify-end mt-4">
              <BaseButton
                type="submit"
                kind="primary"
                size="lg"
                prepend-icon="mdi-check"
                :loading="submitting"
                :disabled="submitting || !canSubmit"
              >
                {{ t('tasks.submit.submit') }}
              </BaseButton>
            </div>
          </div>
        </v-form>
      </v-card-text>
    </v-card>

    <v-card flat rounded="lg" class="mt-6">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-icon color="info" size="28">mdi-information-outline</v-icon>
          </div>
        </template>
        <v-card-title class="text-h6 ps-0">{{ t('tasks.submit.guideTitle') }}</v-card-title>
      </v-card-item>

      <v-card-text>
        <v-list>
          <v-list-item prepend-icon="mdi-check-circle-outline" class="ps-2">
            <v-list-item-title>{{ t('tasks.submit.guideRequirements') }}</v-list-item-title>
          </v-list-item>
          <v-list-item prepend-icon="mdi-file-upload-outline" class="ps-2">
            <v-list-item-title>{{ t('tasks.submit.guideFileSize') }}</v-list-item-title>
          </v-list-item>
          <v-list-item v-if="taskData?.resubmittable" prepend-icon="mdi-refresh" class="ps-2">
            <v-list-item-title>{{ t('tasks.submit.guideMultiple') }}</v-list-item-title>
          </v-list-item>
          <v-list-item v-else prepend-icon="mdi-alert-circle-outline" class="ps-2">
            <v-list-item-title class="text-warning">{{ t('tasks.submit.guideOnce') }}</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-card-text>
    </v-card>

    <!-- 上传进度对话框 -->
    <v-dialog v-model="progressDialogModel" persistent :max-width="DIALOG_WIDTH.md" class="upload-progress-dialog">
      <v-card rounded="lg" class="pa-6">
        <v-card-title class="text-h6 d-flex align-center pb-3">
          <v-icon color="primary" class="mr-3">mdi-cloud-upload</v-icon>
          {{ t('tasks.submit.uploadingTitle') }}
        </v-card-title>

        <v-card-text class="pt-3">
          <div class="mb-6">
            <div class="d-flex align-center justify-space-between mb-2">
              <div class="text-body-1 text-medium-emphasis">
                {{ currentFileName }}
              </div>
              <div
                class="text-body-2 font-weight-medium"
                :class="uploadProgress === 100 ? 'text-success' : 'text-primary'"
              >
                {{ uploadProgress }}%
              </div>
            </div>

            <v-progress-linear
              :model-value="uploadProgress"
              height="8"
              color="primary"
              rounded
              :stream="uploadProgress < 100"
            ></v-progress-linear>

            <div class="d-flex align-center justify-space-between mt-3 text-body-2 text-medium-emphasis">
              <div class="d-flex align-center">
                <v-icon size="small" class="mr-1">mdi-speedometer</v-icon>
                {{ uploadSpeed }}
              </div>
              <div class="d-flex align-center">
                <v-icon size="small" class="mr-1">mdi-clock-outline</v-icon>
                {{ t('tasks.submit.remaining', { time: timeRemaining }) }}
              </div>
            </div>
          </div>

          <div class="text-body-2 text-medium-emphasis text-center">{{ t('tasks.submit.uploadNext') }}</div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// 「提交作业」这一块**画的那一半**：可提交身份的选择、按 schema 渲染的表单、上传进度框。
//
// 取数（查这个身份交没交过）、上传、提交、跳转都在容器 `Submit.vue` 里；这里只吃
// props、只把填好的内容往上发。表单自己那份草稿（哪个身份、每一格填了什么）是纯 UI
// 状态，留在这里。
import type { TaskParticipationInfo } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import CountdownTimer from '@/components/common/CountdownTimer.vue'
import { t } from '@/i18n'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'

/** 一格填好的内容：文字题是一段文本，文件题是一个文件。 */
interface SubmissionEntry {
  contentText?: string
  contentAttachment?: File
}

const props = defineProps<{
  taskData: Task | null
  participationInfo?: TaskParticipationInfo
  /** 这个身份已经交过一版、题又不可重复提交 —— 由容器问服务端得出。 */
  reachedSubmissionLimit: boolean
  submitting: boolean
  progressDialog: boolean
  uploadProgress: number
  currentFileName: string
  uploadSpeed: string
  timeRemaining: string
}>()

const emit = defineEmits<{
  submit: [payload: { content: SubmissionEntry[]; identityId: number }]
  'update:progressDialog': [value: boolean]
}>()

const routeNames = TASK_ROUTE_NAMES
const formRef = ref(null)
const selectedIdentityId = ref<number | null>(null)

// 计算有效的提交身份（已通过审核且可提交的）
const submissionIdentities = computed(() => {
  if (!props.participationInfo?.identities) return []
  return props.participationInfo.identities.filter((id) => id.approved === 'APPROVED' && id.canSubmit)
})

// 是否有有效的提交身份
const hasValidIdentity = computed(() => {
  return submissionIdentities.value.length > 0
})

// 当前选中的身份
const currentIdentity = computed(() => {
  if (!selectedIdentityId.value || !submissionIdentities.value.length) {
    return submissionIdentities.value[0] || null
  }
  return submissionIdentities.value.find((id) => id.id === selectedIdentityId.value) || submissionIdentities.value[0]
})

// 是否可以提交
const canSubmit = computed(() => {
  return props.taskData?.submittable && hasValidIdentity.value && !props.reachedSubmissionLimit
})

// 提交数据
const submissionSchema = computed(() => props.taskData?.submissionSchema || [])
const submissionContent = ref<SubmissionEntry[]>(
  props.taskData?.submissionSchema?.map(() => ({ contentText: '', contentAttachment: undefined })) || []
)

// 用户截止时间
const userDeadline = computed(() => currentIdentity.value?.deadline ?? null)

// 进度对话框：真源在容器，这里用可写代理把它接成 v-model。
const progressDialogModel = computed({
  get: () => props.progressDialog,
  set: (value: boolean) => emit('update:progressDialog', value),
})

onMounted(() => {
  if (props.taskData) {
    // 如果有有效的提交身份，初始化选择第一个
    if (submissionIdentities.value.length > 0) {
      selectedIdentityId.value = submissionIdentities.value[0].id
    }
  }
})

// 监听身份变化
watch(
  () => props.participationInfo,
  (newVal) => {
    if (newVal?.identities && newVal.identities.length > 0) {
      // 尝试查找有效的提交身份
      const validIdentity = newVal.identities.find((id) => id.approved === 'APPROVED' && id.canSubmit)
      if (validIdentity) {
        selectedIdentityId.value = validIdentity.id
      } else if (newVal.identities.length > 0) {
        selectedIdentityId.value = newVal.identities[0].id
      }
    }
  },
  { immediate: true }
)

// 获取类型展示文本
const getTypeText = (type: string) => {
  switch (type) {
    case 'TEXT':
      return t('tasks.submit.typeText')
    case 'FILE':
      return t('tasks.submit.typeFile')
    default:
      return type
  }
}

// 获取类型对应的颜色
const getTypeColor = (type: string) => {
  switch (type) {
    case 'TEXT':
      return 'primary'
    case 'FILE':
      return 'success'
    default:
      return 'grey'
  }
}

const onSubmit = () => {
  if (!currentIdentity.value) return
  emit('submit', { content: submissionContent.value, identityId: currentIdentity.value.id })
}
</script>

<style scoped>
.submission-input {
  transition: transform 0.2s ease;
}

.submission-input:focus-within {
  transform: translateY(-2px);
}

.border-bottom {
  border-bottom: 1px solid rgba(var(--v-border-opacity), var(--v-border-opacity-variant));
}

.form-container {
  max-width: 900px;
  margin: 0 auto;
}

.upload-progress-dialog :deep(.v-card) {
  overflow: hidden;
}
</style>
