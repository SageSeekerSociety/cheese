<template>
  <!-- 实名验证对话框 -->
  <v-dialog
    :model-value="verifyInfoDialogOpen"
    persistent
    max-width="600"
    class="verify-info-dialog"
    scrollable
    @update:model-value="handleCloseVerify"
  >
    <v-card rounded="lg" elevation="4">
      <v-card-title class="text-h5 pa-4 pb-2">
        <div class="d-flex align-center">
          <v-icon color="primary" class="mr-2" size="26">mdi-account-check</v-icon>
          {{ taskData?.requireRealName ? t('tasks.verifyDialog.titleRealName') : t('tasks.verifyDialog.title') }}
        </div>
      </v-card-title>
      <v-card-text class="pa-4 pt-2">
        <div class="text-body-1 mb-4">
          <template v-if="taskData?.requireRealName">
            {{ t('tasks.verifyDialog.introRealName') }}
          </template>
          <template v-else>{{ t('tasks.verifyDialog.intro') }}</template>
        </div>

        <!-- 信息获取提示卡片 - 仅在需要实名时显示 -->
        <v-card
          v-if="taskData?.requireRealName"
          class="mb-4 info-alert-card"
          variant="flat"
          rounded="lg"
          color="surface-light"
        >
          <v-card-text class="pa-3">
            <div class="d-flex align-start">
              <v-avatar size="36" color="info" class="mr-3 info-avatar">
                <!-- 状态色底上的反白图标一律用 surface：这些底色深色下会提亮，白色会糊住 -->
                <v-icon icon="mdi-account-details" color="surface" size="20"></v-icon>
              </v-avatar>
              <div>
                <div class="text-subtitle-2 font-weight-medium mb-1">
                  {{ t('tasks.verifyDialog.realNameConfirmTitle') }}
                </div>
                <p class="text-body-2 mb-0">
                  {{ t('tasks.verifyDialog.realNameConfirmBody') }}
                </p>
              </div>
            </div>
          </v-card-text>
        </v-card>

        <v-tooltip v-if="taskData?.teamLockingPolicy === 'LOCK_ON_APPROVAL'" location="top">
          <template #activator="{ props }">
            <v-card
              class="mb-4 info-alert-card cursor-pointer"
              variant="flat"
              rounded="lg"
              color="surface-light"
              v-bind="props"
            >
              <v-card-text class="pa-3">
                <div class="d-flex align-center">
                  <v-avatar size="36" color="warning" class="mr-3 warning-avatar">
                    <v-icon icon="mdi-lock-check" color="surface" size="18"></v-icon>
                  </v-avatar>
                  <div class="flex-grow-1">
                    <i18n-t scope="global" keypath="tasks.verifyDialog.lockNotice" tag="span" class="text-body-2">
                      <template #policy>
                        <strong>{{ t('tasks.form.teamLockingPolicyLockOnApproval') }}</strong>
                      </template>
                    </i18n-t>
                  </div>
                  <v-icon size="small" color="info">mdi-information-outline</v-icon>
                </div>
              </v-card-text>
            </v-card>
          </template>
          <div class="pa-2">
            <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.verifyDialog.lockTitle') }}</div>
            <p class="text-body-2 mb-0">
              •
              <i18n-t scope="global" keypath="tasks.verifyDialog.lockLine1" tag="span">
                <template #locked>
                  <strong>{{ t('tasks.verifyDialog.locked') }}</strong>
                </template>
              </i18n-t>
              <br />
              • {{ t('tasks.verifyDialog.lockLine2') }}<br />
              • {{ t('tasks.verifyDialog.lockLine3') }}
            </p>
          </div>
        </v-tooltip>

        <v-tooltip v-else-if="taskData?.submitterType === 'TEAM'" location="top">
          <template #activator="{ props }">
            <v-card
              class="mb-4 info-alert-card cursor-pointer"
              variant="flat"
              rounded="lg"
              color="surface-light"
              v-bind="props"
            >
              <v-card-text class="pa-3">
                <div class="d-flex align-center">
                  <v-avatar size="36" color="info" class="mr-3 info-avatar">
                    <v-icon icon="mdi-account-group" color="surface" size="18"></v-icon>
                  </v-avatar>
                  <span class="text-body-2">{{ t('tasks.verifyDialog.rosterRecorded') }}</span>
                  <v-icon size="small" color="info" class="ms-auto">mdi-information-outline</v-icon>
                </div>
              </v-card-text>
            </v-card>
          </template>
          <div class="pa-2">
            <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.verifyDialog.rosterTitle') }}</div>
            <p class="text-body-2 mb-0">
              • {{ t('tasks.verifyDialog.rosterLine1') }}<br />
              • {{ t('tasks.verifyDialog.rosterLine2') }}
            </p>
          </div>
        </v-tooltip>

        <VerifyInfoFormComponent
          ref="verifyInfoFormRef"
          data-role="verify-info-form"
          :require-real-name="taskData?.requireRealName ?? false"
          @submit="handleSubmitVerify"
        />
        <div class="privacy-consent">
          <v-checkbox
            v-if="taskData?.requireRealName"
            v-model="privacyAgreedProxy"
            color="primary"
            hide-details
            class="privacy-checkbox"
            density="compact"
          >
            <template #label>
              <div class="d-flex align-center">
                <i18n-t scope="global" keypath="tasks.verifyDialog.consent" tag="span">
                  <template #statement>
                    <span class="text-primary font-weight-medium privacy-link" @click.stop.prevent="directShowPrivacy">
                      {{ t('tasks.verifyDialog.privacyStatement') }}
                    </span>
                  </template>
                </i18n-t>
                <v-tooltip location="end" max-width="300">
                  <template #activator="{ props }">
                    <v-icon size="small" color="primary" class="ms-1" v-bind="props"> mdi-information-outline </v-icon>
                  </template>
                  <span>{{ t('tasks.verifyDialog.privacyTip') }}</span>
                </v-tooltip>
              </div>
            </template>
          </v-checkbox>
        </div>

        <!-- 确认领取之前，把「会继承什么」摆出来 (#944)：资源包、合成后的指导
             （连来自哪一层）、以及会被带上的资料。这就是决定之前该看的那一页。 -->
        <TaskInheritance :inheritance="inheritance" :loading="inheritanceLoading" />
      </v-card-text>
      <v-card-actions class="pa-4 pt-0">
        <v-spacer></v-spacer>
        <BaseButton kind="ghost" @click="handleCloseVerify">{{ t('global.cancel') }}</BaseButton>
        <BaseButton kind="primary" @click="submitVerifyForm">{{ t('tasks.verifyDialog.confirmJoin') }}</BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 隐私声明对话框 -->
  <v-dialog
    v-if="taskData?.requireRealName"
    :model-value="privacyDialogOpen"
    max-width="560"
    scrollable
    @update:model-value="handleCancelPrivacy"
  >
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center px-4 pt-4 pb-2">
        <v-icon color="primary" class="mr-3" size="28">mdi-shield-check</v-icon>
        <span class="text-h5 font-weight-medium">{{ t('tasks.verifyDialog.privacyTitle') }}</span>
      </v-card-title>

      <v-card-text class="px-4 pb-2">
        <p class="text-subtitle-2 font-weight-medium mb-4">{{ t('tasks.verifyDialog.privacyIntro') }}</p>

        <!-- 隐私信息保护区域 -->
        <PrivacyProtectionInfo />
      </v-card-text>

      <v-card-actions class="pa-4 pt-2">
        <v-spacer></v-spacer>
        <BaseButton v-if="!fromSubmit" kind="ghost" @click="handleCancelPrivacy">{{
          t('tasks.verifyDialog.understood')
        }}</BaseButton>
        <template v-else>
          <BaseButton kind="ghost" @click="handleCancelPrivacy">{{ t('tasks.verifyDialog.notNow') }}</BaseButton>
          <BaseButton kind="primary" @click="confirmPrivacy">{{ t('tasks.verifyDialog.agreeAndJoin') }}</BaseButton>
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 新增队伍选择对话框 -->
  <TeamSelectionDialog
    :open="teamSelectionDialogOpen"
    :task-data="taskData"
    :available-teams="availableTeams"
    :loading="loadingTeams"
    @close="handleCloseTeamSelection"
    @select="handleSelectTeam"
  />

  <!-- 退出小队对话框 -->
  <LeaveTeamDialog
    :open="leaveTeamDialogOpen"
    :task-data="taskData"
    :loading="loadingTeams"
    :joined-teams="joinedTeams"
    :selected-team-id="selectedLeaveTeamId"
    @close="handleCancelLeaveTeam"
    @select="handleSelectLeaveTeam"
    @confirm="handleConfirmLeaveTeam"
  />
</template>

<script setup lang="ts">
import type { Task, Team, TeamTaskEligibility } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useTaskInheritance } from '../composables/useTaskInheritance'
import { useEvents } from '../events'

import TaskInheritance from './TaskInheritance.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

// 组件
const VerifyInfoFormComponent = defineAsyncComponent(() => import('@/components/tasks/VerifyInfoForm.vue'))
const PrivacyProtectionInfo = defineAsyncComponent(() => import('./PrivacyProtectionInfo.vue'))
const TeamSelectionDialog = defineAsyncComponent(() => import('./TeamSelectionDialog.vue'))
const LeaveTeamDialog = defineAsyncComponent(() => import('./LeaveTeamDialog.vue'))

// 只保留必要的props
const props = defineProps<{
  taskData: Task | null
  availableTeams: TeamTaskEligibility[]
  loadingTeams: boolean
  joinedTeams: Team[]
  selectedLeaveTeamId: number | null
  participationInfo: any
}>()

// 使用事件总线
const events = useEvents()

// 领取确认框里那份「会继承什么」(#944)。`taskData` 先是 null（详情还没回来），
// 到位后 watch 自己会去取。
const { inheritance, loading: inheritanceLoading } = useTaskInheritance(() => props.taskData?.id)

// 各种对话框的状态
const verifyInfoDialogOpen = ref(false)
const privacyDialogOpen = ref(false)
const teamSelectionDialogOpen = ref(false)
const leaveTeamDialogOpen = ref(false)
const privacyAgreed = ref(false)
const fromSubmit = ref(false)

// 组件引用
const verifyInfoFormRef = ref<InstanceType<typeof VerifyInfoFormComponent> | null>(null)

// 监听对话框状态变化
onMounted(() => {
  events.on('verify-dialog-open', (value) => {
    verifyInfoDialogOpen.value = value
  })

  events.on('privacy-dialog-open', (value) => {
    privacyDialogOpen.value = value
  })

  events.on('team-selection-dialog-open', (value) => {
    teamSelectionDialogOpen.value = value
  })

  events.on('leave-team-dialog-open', (value) => {
    leaveTeamDialogOpen.value = value
  })

  events.on('show-privacy', () => {
    privacyDialogOpen.value = true
  })

  events.on('privacy-agreed-change', (value) => {
    privacyAgreed.value = value
  })

  events.on('set-from-submit', (value) => {
    fromSubmit.value = value
  })
})

const privacyAgreedProxy = computed({
  get: () => privacyAgreed.value,
  set: (value) => {
    if (privacyAgreed.value !== value) {
      privacyAgreed.value = value
      events.emit('privacy-agreed-change', value)
    }
  },
})

// 提交表单
const submitVerifyForm = async () => {
  console.log('提交表单', props.taskData?.requireRealName, privacyAgreedProxy.value)
  // 如果需要实名并且没有同意隐私协议，先弹出隐私协议
  if (props.taskData?.requireRealName && !privacyAgreedProxy.value) {
    console.log('需要先同意隐私协议')
    events.emit('show-privacy', undefined)
    events.emit('set-from-submit', true)
    fromSubmit.value = true
    return
  }

  // 已同意隐私协议或不需要实名，直接提交
  if (verifyInfoFormRef.value) {
    console.log('提交表单', verifyInfoFormRef.value)
    verifyInfoFormRef.value.submit()
  } else {
    console.error('找不到有效的表单引用')
  }
}

// 处理表单提交
const handleSubmitVerify = (data: any) => {
  events.emit('submit-verify', data)
  verifyInfoDialogOpen.value = false
}

// 确认隐私协议
const confirmPrivacy = () => {
  events.emit('confirm-privacy', { fromSubmit: fromSubmit.value })
  privacyAgreed.value = true
  privacyDialogOpen.value = false

  // 如果是从提交按钮触发的，在隐私对话框关闭后继续提交表单
  if (fromSubmit.value) {
    setTimeout(() => {
      if (verifyInfoFormRef.value) {
        verifyInfoFormRef.value.submit()
      }
    }, 300)
  }
}

const handleCloseVerify = () => {
  verifyInfoDialogOpen.value = false
  events.emit('verify-dialog-open', false)
}

const handleCancelPrivacy = () => {
  privacyDialogOpen.value = false
  fromSubmit.value = false
  events.emit('cancel-privacy', undefined)
}

const handleCloseTeamSelection = () => {
  teamSelectionDialogOpen.value = false
  events.emit('team-selection-dialog-open', false)
}

const handleCancelLeaveTeam = () => {
  leaveTeamDialogOpen.value = false
  events.emit('leave-team-dialog-open', false)
}

const handleSelectTeam = (teamId: number) => {
  events.emit('select-team', teamId)
}

const handleSelectLeaveTeam = (teamId: number) => {
  events.emit('select-leave-team', teamId)
}

const handleConfirmLeaveTeam = () => {
  events.emit('confirm-leave-team', undefined)
  leaveTeamDialogOpen.value = false
}

// 直接显示隐私声明
const directShowPrivacy = () => {
  events.emit('show-privacy', undefined)
  privacyDialogOpen.value = true
}
</script>

<style scoped>
.info-alert-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  transition: all 0.2s ease;
}

.info-avatar {
  background: linear-gradient(135deg, rgb(var(--v-theme-info)), rgb(var(--v-theme-info)));
  box-shadow: 0 2px 4px rgba(var(--v-theme-info), 0.2);
}

.warning-avatar {
  background: linear-gradient(135deg, rgb(var(--v-theme-warning)), rgb(var(--v-theme-warning)));
  box-shadow: 0 2px 4px rgba(var(--v-theme-warning), 0.2);
}

.privacy-link {
  cursor: pointer;
  transition: all 0.2s ease;
  border-bottom: 1px dashed rgba(var(--v-theme-primary), 0.5);
}

.privacy-link:hover {
  border-bottom-color: rgb(var(--v-theme-primary));
  opacity: 0.9;
}

.privacy-checkbox :deep(.v-label) {
  opacity: 1;
}

.verify-info-dialog :deep(.v-card) {
  overflow: hidden;
}

.cursor-pointer {
  cursor: pointer;
}
</style>
