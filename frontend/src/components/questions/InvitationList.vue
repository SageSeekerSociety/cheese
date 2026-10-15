<template>
  <v-list>
    <v-list-item v-for="user in people" :key="user.id" :title="user.nickname" :subtitle="user.intro">
      <template #prepend>
        <!-- 种子用 handle(username)：颜色跟着人走，不跟昵称走，改昵称不换色（契约 §3.14）。 -->
        <user-avatar :avatar="getAvatarUrl(user.avatarId)" :name="user.nickname" :seed="user.username" />
      </template>
      <template #append>
        <BaseButton
          :kind="isInvited(user) ? 'ghost' : 'secondary'"
          :disabled="isInvited(user)"
          @click="emit('invite', user)"
        >
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
// 「邀请谁来回答」弹窗里那份名单的画面：一行一个人，右边一颗邀请按钮；已经邀请过的人
// 那颗按钮写「已邀请」并且按不动。名单、已经邀请过谁、点下按钮发哪趟请求都在页面
// （`views/question/Detail.vue`）那边，这里只收结果、把点的那个人交上去。
import type { User } from '@/types'

import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import UserAvatar from '../common/UserAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

const props = defineProps<{
  /** 可以邀请谁。 */
  people: User[]
  /** 已经邀请过的人的 id：推荐名单不过滤他们，所以得靠这一份比对出「这颗按钮是不是已经点过」。 */
  invitedIds: number[]
}>()

const emit = defineEmits<{
  invite: [user: User]
}>()

const isInvited = (user: User): boolean => props.invitedIds.includes(user.id)
</script>
