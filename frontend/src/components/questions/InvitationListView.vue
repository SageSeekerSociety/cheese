<template>
  <v-list>
    <v-list-item v-for="(user, index) in users" :key="user.id" :title="user.nickname" :subtitle="user.intro">
      <template #prepend>
        <user-avatar :avatar="getAvatarUrl(user.avatarId)" />
      </template>
      <template #append>
        <BaseButton
          :kind="isInvited[index] ? 'ghost' : 'secondary'"
          :disabled="isInvited[index]"
          @click="$emit('invite', index)"
        >
          <v-icon class="me-2">mdi-account-multiple-plus</v-icon>
          {{
            isInvited[index]
              ? t('questions.invitationList.buttons.invited')
              : t('questions.invitationList.buttons.invite')
          }}
        </BaseButton>
      </template>
    </v-list-item>
  </v-list>
</template>

<script setup lang="ts">
// 「邀请回答」名单**画的那一半**：只认 props、只往上发事件（请第几行）。
//
// 拉名单与请人（`composables/useInvitationList`）留在 `InvitationList.vue` 和问题
// 详情容器里。
import type { User } from '@/types'

import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import UserAvatar from '../common/UserAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

defineProps<{
  users: User[]
  /** 与 `users` 一一对应：这一位是不是已经请过了。 */
  isInvited: boolean[]
}>()

defineEmits<{ invite: [index: number] }>()
</script>
