<template>
  <div class="notification-content">
    <div class="announcement-head">
      <span class="announcement-tag">{{ t('notifications.SPACE_ANNOUNCEMENT.tag') }}</span>
      <span class="text-subtitle-2 font-weight-bold">{{ title }}</span>
    </div>
    <div v-if="excerpt" class="text-body-2 text-medium-emphasis mt-1 announcement-excerpt">{{ excerpt }}</div>
    <div class="text-caption text-medium-emphasis mt-1">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getStringMetadata } from './NotificationRenderUtils'

/**
 * 空间里发了一条公告。标题、正文第一行，底下是哪个空间、谁发的；点开去那个空间的
 * 公告页。只进站内动态，不发邮件、不推送。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const text = (key: string) => getStringMetadata(props.notification, key)

const title = computed(() => text('title'))
const excerpt = computed(() => text('excerpt'))
const body = computed(() => {
  const author = text('authorName')
  const space = text('spaceName')
  return author ? t('notifications.SPACE_ANNOUNCEMENT.meta', { space, author }) : space
})

const routerLink = computed(() => {
  const spaceId = text('spaceId')
  return spaceId ? { name: 'SpacesAnnouncements', params: { spaceId } } : undefined
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
  min-width: 0;
}

.announcement-head {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.announcement-tag {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  line-height: 20px;
}

.announcement-excerpt {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
