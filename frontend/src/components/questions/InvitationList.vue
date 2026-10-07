<template>
  <v-list>
    <v-list-item v-for="user in people" :key="user.id" :title="user.nickname" :subtitle="user.intro">
      <template #prepend>
        <!-- 种子用 handle(username)：颜色跟着人走，不跟昵称走，改昵称不换色（契约 §3.14）。 -->
        <user-avatar :avatar="getAvatarUrl(user.avatarId)" :name="user.nickname" :seed="user.username" />
      </template>
      <template #append>
        <BaseButton :kind="isInvited(user) ? 'ghost' : 'secondary'" :disabled="isInvited(user)" @click="invite(user)">
          <v-icon class="me-2">mdi-account-multiple-plus</v-icon>
          {{
            isInvited(user)
              ? t('questions.invitationList.buttons.invited')
              : t('questions.invitationList.buttons.invite')
          }}
        </BaseButton>
      </template>
    </v-list-item>
  </v-list>
</template>

<script setup lang="ts">
import { toRefs } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import { useQuestionInvitations } from '@/composables/useQuestionInvitations'

import UserAvatar from '../common/UserAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

const props = defineProps<{
  questionId: number
}>()

// 名单、已邀请的人和发邀请都在 `useQuestionInvitations` 里（组件不吃 API 层）：
// 这里只画它交回来的那几个状态。
const { questionId } = toRefs(props)
const { people, isInvited, invite } = useQuestionInvitations(questionId)
</script>
