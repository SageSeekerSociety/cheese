<template>
  <!-- 发来通知的那个人画成他自己的头像；他没有挑过头像就画彩色首字母 —— 一格空白
       或一张所有人的共用脸，都不如「这个人的首字母和他的颜色」。没有可画的人时，
       画一个这一类事的淡色小图标：它只是装饰，不垫色块，免得一列同样的色块成了这一行
       最抢眼的东西。 -->
  <UserAvatar
    v-if="face"
    :size="size"
    :avatar="face.avatarUrl ?? ''"
    :name="face.name"
    :seed="face.handle || face.name"
    :kind="face.type === 'user' ? 'person' : 'org'"
  />
  <v-icon v-else :icon="icon" size="16" class="notification-avatar__icon" />
</template>

<script setup lang="ts">
import type { Notification } from '@/types/apiNotifications'
import type { EntityInfo } from '@/types/apiNotifications'

import { computed } from 'vue'

import { getNotificationIcon } from './registry'

import UserAvatar from '@/components/common/UserAvatar.vue'

const props = defineProps<{
  notification: Notification
  size?: number | string
}>()

// 待办页的行左边那一格是 20px。
const size = computed(() => props.size || 20)

const icon = computed(() => getNotificationIcon(props.notification))

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

<style scoped>
.notification-avatar__icon {
  color: var(--faint);
}
</style>
