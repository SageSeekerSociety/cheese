<template>
  <!-- 发来通知的那个人画成他自己的头像；他没有挑过头像就画彩色首字母 —— 一格空白
       或一张所有人的共用脸，都不如「这个人的首字母和他的颜色」。没有可画的人时，
       画的才是通知类型的图标。 -->
  <UserAvatar
    v-if="face"
    :size="size"
    :avatar="face.avatarUrl ?? ''"
    :name="face.name"
    :seed="face.handle || face.name"
    :kind="face.type === 'user' ? 'person' : 'org'"
  />
  <v-avatar v-else :size="size" :color="color" variant="tonal">
    <v-icon :icon="icon" :size="iconSize" :color="color"></v-icon>
  </v-avatar>
</template>

<script setup lang="ts">
import type { Notification } from '@/network/api/notifications/types'
import type { EntityInfo } from '@/network/api/notifications/types'

import { computed } from 'vue'

import UserAvatar from '@/components/common/UserAvatar.vue'
import { getNotificationMark } from '@/services/notification/registry'

const props = defineProps<{
  notification: Notification
  size?: number | string
  color?: string
}>()

// 默认头像大小
const size = computed(() => props.size || 36)

// 图标大小
const iconSize = computed(() => {
  const avatarSize = typeof props.size === 'number' ? props.size : parseInt(props.size as string, 10)
  return isNaN(avatarSize) ? 18 : Math.max(Math.floor(avatarSize / 2), 16)
})

// 图标和颜色跟着这一条现在的状态走，不只看类型：答过的提问不再画成待处理
const mark = computed(() => getNotificationMark(props.notification))
const icon = computed(() => mark.value.icon)
const color = computed(() => props.color || mark.value.color)

// 这一条通知该画谁的脸。
//
// 先按角色找**人**（发送者优先），找到一个就用他：他有没有头像都要用 —— 没有头像时
// 画的是他的彩色首字母，那也比退回类型图标更认得出是谁。这里必须判 `type === 'user'`：
// 一条通知的实体里既有团队也有人，团队的头像画成圆形、和「谁发来的」也不是一回事。
// 角色表里一个能画的人都没有，才退到「其它带头像的实体」（例如一张团队邀请卡上的
// 团队），那时按团队画圆角方块，和平台别处的团队头像一个样子。
const face = computed<EntityInfo | null>(() => {
  const { entities } = props.notification
  const possibleRoles = [
    'sender',
    'mentioner',
    'replier',
    'reactor',
    'inviter',
    'requester',
    'approver',
    'rejector',
    'accepter',
    'decliner',
    'canceler',
  ]

  for (const role of possibleRoles) {
    const entity = entities[role]
    if (entity && entity.type === 'user') {
      return entity
    }
  }

  for (const entity of Object.values(entities)) {
    if (entity && entity.avatarUrl) {
      return entity
    }
  }

  return null
})
</script>
