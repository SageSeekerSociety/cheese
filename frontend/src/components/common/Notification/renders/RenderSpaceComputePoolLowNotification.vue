<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">{{ title }}</div>
    <div v-if="body" class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getMetadata, getStringMetadata } from './NotificationRenderUtils'

/**
 * 空间的共享额度池快花完了，通知管理员。
 *
 * 收件人是空间管理员而不是花钱的那个学生：池子是老师（或三创中心）买的，能
 * 决定「再充一点还是就这样」的只有他。所以这一条点进去落在充值页上，不是落
 * 在某个项目里。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const spaceId = computed(() => getStringMetadata(props.notification, 'spaceId'))
const ratioPercent = computed(() => Math.round(getMetadata(props.notification, 'ratio', 0) * 100))
const creditsRemaining = computed(() => getMetadata(props.notification, 'creditsRemaining', 0))
const creditsTotal = computed(() => getMetadata(props.notification, 'creditsTotal', 0))

const title = computed(() => t('notifications.SPACE_COMPUTE_POOL_LOW.title', { ratio: ratioPercent.value }))

const body = computed(() =>
  t('notifications.SPACE_COMPUTE_POOL_LOW.body', {
    remaining: creditsRemaining.value.toFixed(2),
    total: creditsTotal.value.toFixed(2),
  })
)

const routerLink = computed(() => {
  if (!spaceId.value) return undefined
  return {
    name: 'SpacesDetailManageComputePool',
    params: { spaceId: spaceId.value },
  }
})

const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: routerLink.value,
}))

defineExpose({
  content,
})
</script>

<style scoped>
.notification-content {
  display: flex;
  flex-direction: column;
}
</style>
