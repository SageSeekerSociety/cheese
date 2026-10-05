<template>
  <v-list class="pa-4">
    <!-- 我发起的申请副标题 -->
    <v-list-subheader>
      {{ t('teams.pending.myRequests') }} <span v-if="!loadingMyRequests">{{ myRequests.length }}</span>
    </v-list-subheader>

    <!-- 我发起的申请 - 加载中 -->
    <div v-if="loadingMyRequests" class="d-flex flex-column align-center py-4">
      <v-progress-circular indeterminate color="primary" :size="40" :width="3" class="mb-3"></v-progress-circular>
      <p class="text-body-2 text-medium-emphasis">{{ t('teams.pending.loading') }}</p>
    </div>

    <BaseLoadError
      v-else-if="failedMyRequests"
      :title="t('teams.pending.loadRequestsFailed')"
      :error="myRequestsError"
      @retry="$emit('retryRequests')"
    />

    <!-- 我发起的申请 - 空状态 -->
    <div v-else-if="!myRequests.length" class="d-flex flex-column align-center py-4">
      <v-avatar size="48" class="bg-surface-light mb-3">
        <v-icon icon="mdi-account-arrow-right" size="large" color="on-surface-variant"></v-icon>
      </v-avatar>
      <p class="text-subtitle-2 font-weight-medium text-center mb-1">{{ t('teams.pending.noRequests') }}</p>
      <p class="text-caption text-center text-medium-emphasis">{{ t('teams.pending.noRequestsHint') }}</p>
    </div>

    <!-- 我发起的申请列表 -->
    <v-list-item
      v-for="request in myRequests"
      :key="`request-${request.id}`"
      rounded="lg"
      class="mb-2 application-item"
    >
      <template #prepend>
        <v-avatar size="40" class="mr-3">
          <v-img :src="getAvatarUrl(request.team.avatarId)"></v-img>
        </v-avatar>
      </template>
      <v-list-item-title class="d-flex align-center">
        {{ request.team.name }}
        <v-chip :color="getStatusColor(request.status)" size="x-small" class="ml-2" variant="tonal">
          {{ getStatusText(request.status) }}
        </v-chip>
      </v-list-item-title>
      <v-list-item-subtitle class="text-caption">
        {{ t('teams.pending.requestedAt', { time: formatDate(request.createdAt) }) }}
      </v-list-item-subtitle>

      <template #append>
        <BaseButton
          v-if="request.status === 'PENDING'"
          kind="ghost"
          size="sm"
          icon="mdi-close"
          :aria-label="t('global.cancel')"
          @click="$emit('cancelRequest', request.id)"
        />
      </template>
    </v-list-item>

    <!-- 分隔符 -->
    <v-divider v-if="myRequests.length || myInvitations.length" class="my-4"></v-divider>

    <!-- 收到的邀请副标题 -->
    <v-list-subheader>
      {{ t('teams.pending.myInvitations') }} <span v-if="!loadingMyInvitations">{{ myInvitations.length }}</span>
    </v-list-subheader>

    <!-- 收到的邀请 - 加载中 -->
    <div v-if="loadingMyInvitations" class="d-flex flex-column align-center py-4">
      <v-progress-circular indeterminate color="primary" :size="40" :width="3" class="mb-3"></v-progress-circular>
      <p class="text-body-2 text-medium-emphasis">{{ t('teams.pending.loading') }}</p>
    </div>

    <BaseLoadError
      v-else-if="failedMyInvitations"
      :title="t('teams.pending.loadInvitationsFailed')"
      :error="myInvitationsError"
      @retry="$emit('retryInvitations')"
    />

    <BaseEmptyState
      v-else-if="!myInvitations.length"
      size="compact"
      icon="mdi-email-outline"
      :title="t('teams.pending.noInvitations')"
      :desc="t('teams.pending.noInvitationsHint')"
    />

    <!-- 收到的邀请列表 -->
    <v-list-item
      v-for="invitation in myInvitations"
      :key="`invitation-${invitation.id}`"
      rounded="lg"
      class="mb-2 application-item"
    >
      <template #prepend>
        <v-avatar size="40" class="mr-3">
          <v-img :src="getAvatarUrl(invitation.team.avatarId)"></v-img>
        </v-avatar>
      </template>
      <v-list-item-title class="d-flex align-center">
        {{ invitation.team.name }}
        <v-chip :color="getStatusColor(invitation.status)" size="x-small" class="ml-2" variant="tonal">
          {{ getStatusText(invitation.status) }}
        </v-chip>
      </v-list-item-title>
      <v-list-item-subtitle class="text-caption">
        {{ t('teams.pending.invitedAt', { time: formatDate(invitation.createdAt) }) }}
      </v-list-item-subtitle>

      <template #append>
        <div v-if="invitation.status === 'PENDING'" class="d-flex">
          <BaseButton
            kind="primary"
            size="sm"
            icon="mdi-check"
            :title="t('teams.pending.accept')"
            class="mr-4"
            @click="$emit('acceptInvitation', invitation.id)"
          />
          <BaseButton
            kind="danger"
            size="sm"
            icon="mdi-close"
            :title="t('teams.pending.decline')"
            @click="$emit('declineInvitation', invitation.id)"
          />
        </div>
      </template>
    </v-list-item>

    <v-divider class="my-4"></v-divider>

    <!-- 收到的项目邀请。放在这一页，是因为这一页就是「等你答复的事」的那一页；而
         被邀请的人还不在那个项目里，项目里的任何界面他都够不着。接在小队那两段
         后面而不是插在中间——那两段是一对（我发起的 / 我收到的），劈开读起来像是
         漏了一半。 -->
    <v-list-subheader>
      {{ t('teams.pending.projectInvitations') }}
      <span v-if="!loadingProjectInvitations">{{ projectInvitations.length }}</span>
    </v-list-subheader>

    <div v-if="loadingProjectInvitations" class="d-flex flex-column align-center py-4">
      <v-progress-circular indeterminate color="primary" :size="40" :width="3" class="mb-3"></v-progress-circular>
      <p class="text-body-2 text-medium-emphasis">{{ t('teams.pending.loading') }}</p>
    </div>

    <BaseLoadError
      v-else-if="failedProjectInvitations"
      :title="t('teams.pending.loadProjectInvitationsFailed')"
      :error="projectInvitationsError"
      @retry="$emit('retryProjectInvitations')"
    />

    <div v-else-if="!projectInvitations.length" class="d-flex flex-column align-center py-4">
      <v-avatar size="48" class="bg-surface-light mb-3">
        <v-icon icon="mdi-folder-account-outline" size="large" color="on-surface-variant"></v-icon>
      </v-avatar>
      <p class="text-subtitle-2 font-weight-medium text-center mb-1">{{ t('teams.pending.noProjectInvitations') }}</p>
      <p class="text-caption text-center text-medium-emphasis">{{ t('teams.pending.noProjectInvitationsHint') }}</p>
    </div>

    <v-list-item
      v-for="invitation in projectInvitations"
      :key="`project-invitation-${invitation.id}`"
      rounded="lg"
      class="mb-2 application-item"
    >
      <template #prepend>
        <v-avatar size="40" class="mr-3 bg-surface-light">
          <v-icon icon="mdi-folder-outline" color="on-surface-variant"></v-icon>
        </v-avatar>
      </template>
      <v-list-item-title>{{ invitation.project_name || t('teams.pending.unnamedProject') }}</v-list-item-title>
      <v-list-item-subtitle class="text-caption">
        <i18n-t scope="global" keypath="teams.pending.projectInvitedBy" tag="span">
          <template #inviter>
            <UserRef
              :handle="invitation.inviter_handle"
              :name="resolveUser(invitation.inviter_handle).name"
              :to="resolveUser(invitation.inviter_handle).to"
              @navigate="$emit('navigate', resolveUser(invitation.inviter_handle).to)"
            />
          </template>
        </i18n-t>
      </v-list-item-subtitle>

      <template #append>
        <div class="d-flex">
          <BaseButton
            kind="primary"
            size="sm"
            icon="mdi-check"
            :title="t('teams.pending.accept')"
            class="mr-4"
            :disabled="answering === invitation.id"
            @click="$emit('answerProjectInvitation', invitation, true)"
          />
          <BaseButton
            kind="danger"
            size="sm"
            icon="mdi-close"
            :title="t('teams.pending.decline')"
            :disabled="answering === invitation.id"
            @click="$emit('answerProjectInvitation', invitation, false)"
          />
        </div>
      </template>
    </v-list-item>
  </v-list>
</template>

<script setup lang="ts">
// 「待办 / 申请与邀请」这一页**画的那一半**：三段（我发起的、我收到的、项目邀请）、
// 每段的加载 / 失败 / 空态、以及接受/拒绝的按钮。
//
// 取数（`TeamsApi.*`、`listMyInvitations`）与去处在容器 `Pending.vue` 里；这里只吃
// props、只往上发事件。人名那颗用纯展示的 `UserRef`，名字与去处由容器算好当
// `resolveUser` 递进来。
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { ProjectInvitation } from '@/cx_types'
import type { TeamMembershipApplication } from '@/types'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRef.vue'
import i18n, { t } from '@/i18n'

defineProps<{
  myRequests: TeamMembershipApplication[]
  loadingMyRequests: boolean
  failedMyRequests: boolean
  myRequestsError: string | null
  myInvitations: TeamMembershipApplication[]
  loadingMyInvitations: boolean
  failedMyInvitations: boolean
  myInvitationsError: string | null
  projectInvitations: ProjectInvitation[]
  loadingProjectInvitations: boolean
  failedProjectInvitations: boolean
  projectInvitationsError: string | null
  answering: string | null
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

defineEmits<{
  retryRequests: []
  retryInvitations: []
  retryProjectInvitations: []
  cancelRequest: [requestId: number]
  acceptInvitation: [invitationId: number]
  declineInvitation: [invitationId: number]
  answerProjectInvitation: [invitation: ProjectInvitation, accept: boolean]
  navigate: [target: ResolvedUserRef['to']]
}>()

const { locale } = i18n.global

// 格式化日期
const formatDate = (timestamp: number) => {
  return new Date(timestamp).toLocaleString(locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 获取状态颜色
const getStatusColor = (status: string) => {
  switch (status) {
    case 'PENDING':
      return 'warning'
    case 'APPROVED':
    case 'ACCEPTED':
      return 'success'
    case 'REJECTED':
    case 'DECLINED':
    case 'CANCELED':
      return 'error'
    default:
      return 'grey'
  }
}

// 获取状态文本
const getStatusText = (status: string) => {
  switch (status) {
    case 'PENDING':
      return t('teams.members.status.pending')
    case 'APPROVED':
      return t('teams.members.status.approved')
    case 'ACCEPTED':
      return t('teams.members.status.accepted')
    case 'REJECTED':
      return t('teams.members.status.rejected')
    case 'DECLINED':
      return t('teams.members.status.declined')
    case 'CANCELED':
      return t('teams.members.status.canceled')
    default:
      return t('teams.members.status.unknown')
  }
}
</script>

<style scoped>
.application-item {
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease;
  border: 1px solid transparent;
}

.application-item:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
  border-color: rgba(var(--v-theme-primary), 0.1);
}
</style>
