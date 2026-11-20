<template>
  <!-- One entry of the activity feed, drawn as the inbox page's row (`InboxRow`): the
       sender (or a mark for the kind) on the left, this type's renderer in the middle,
       the time on the right, swapped for mark-read / delete under the pointer. Opening
       it goes where it points and marks it read. -->
  <InboxRow
    class="notification-item"
    :opens="hasRouterLink"
    :time="time"
    :at="at"
    :unread="!notification.read"
    @open="navigateToTarget"
    @menu="rowMenu.open('item', $event)"
  >
    <template #lead>
      <NotificationAvatar :notification="notification" />
    </template>

    <component
      :is="contentComponent"
      ref="contentRef"
      :notification="notification"
      class="notification-item__content"
    />

    <template #hover>
      <BaseButton
        v-if="!notification.read"
        icon="mdi-check"
        size="sm"
        :aria-label="t('notifications.common.markAsRead')"
        :title="t('notifications.common.markAsRead')"
        @click.stop="markAsRead"
      />
      <BaseButton
        icon="mdi-delete-outline"
        size="sm"
        :aria-label="t('notifications.common.delete')"
        :title="t('notifications.common.delete')"
        @click.stop="askDelete"
      />
    </template>
  </InboxRow>

  <AdaptiveMenu v-bind="rowMenu.bind('item')" :actions="itemActions">
    <template #activator />
  </AdaptiveMenu>

  <!-- Deleting cannot be undone, so it asks first (design-system §3.7): the confirm
       button names the action, solid red, never shown on hover. -->
  <ConfirmDialog
    v-model="confirmingDelete"
    :title="t('notifications.common.deleteTitle')"
    :confirm-label="t('notifications.common.delete')"
    danger
    @confirm="doDelete"
  >
    {{ t('notifications.common.deleteHint') }}
  </ConfirmDialog>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import type { MenuAction } from '@/components/common/menuAction'
import type { Notification } from '@/types/apiNotifications'
import type { RenderedNotificationContent } from './renders/NotificationRenderUtils'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useNavigation } from '@/composables/useNavigation'
import { useRowMenu } from '@/composables/useRowMenu'

import InboxRow from './InboxRow.vue'
import NotificationAvatar from './NotificationAvatar.vue'
import { getNotificationRenderer } from './registry'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { toast } from '@/lib/toast'

const props = defineProps<{
  notification: Notification
  /** 右边写的时间，列表按自己的口径写好递进来（动态按天分组，这里只写几点几分）。 */
  time?: string
  onMarkAsRead: (notificationId: number) => void
  onDelete: (notificationId: number) => void
}>()

const { t } = useI18n()

const at = computed(() => new Date(props.notification.createdAt).toISOString())

const contentComponent = computed<Component>(() => getNotificationRenderer(props.notification.type))

// 渲染器把它算好的去处交出来（`defineExpose({ content })`）。
const contentRef = ref<{ content: RenderedNotificationContent } | null>(null)
const routerLink = computed(() => contentRef.value?.content?.routerLink)
const hasRouterLink = computed(() => !!routerLink.value)

// 右键或长按一条：标为已读、删除，弹在那一点上。触屏上那两颗悬停按钮不画，靠的就是它。
const rowMenu = useRowMenu<'item'>()
const itemActions = computed<MenuAction[]>(() => {
  const actions: MenuAction[] = []
  if (!props.notification.read)
    actions.push({
      key: 'read',
      label: t('notifications.common.markAsRead'),
      icon: 'mdi-check-all',
      onSelect: () => props.onMarkAsRead(props.notification.id),
    })
  actions.push({
    key: 'delete',
    label: t('notifications.common.delete'),
    icon: 'mdi-delete-outline',
    danger: true,
    onSelect: () => (confirmingDelete.value = true),
  })
  return actions
})

const markAsRead = () => props.onMarkAsRead(props.notification.id)

// 删除不可撤销，点一下先弹确认框：误触的代价是一条再也回不来的动态。确认之后再真的删。
const confirmingDelete = ref(false)
const askDelete = () => {
  confirmingDelete.value = true
}
const doDelete = () => {
  confirmingDelete.value = false
  props.onDelete(props.notification.id)
}

// 宿主没装路由时整份 nav 是 null，那就没有去处：照旧标已读，什么都不跳。
const nav = useNavigation()

const navigateToTarget = () => {
  const link = routerLink.value
  if (!link) return
  props.onMarkAsRead(props.notification.id)
  nav?.navigate({ name: String(link.name), params: { ...link.params }, query: { ...link.query } })?.catch((error) => {
    if (error.name !== 'NavigationDuplicated') {
      console.error('导航错误:', error)
      toast.error(t('notifications.common.unreachable'))
    }
  })
}
</script>

<style scoped>
/* 渲染器各自画标题、细节和引文，用的是下面这几个类名；样子在这里定一次，和待办页上面
   那一块的行同一套字号和颜色。 */
.notification-item__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.notification-item :deep(.n-title) {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notification-item.inbox-row--unread :deep(.n-title) {
  font-weight: 600;
}

.notification-item :deep(.n-sub) {
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notification-item :deep(.n-quote) {
  overflow: hidden;
  margin-top: 2px;
  padding-left: 8px;
  border-left: 2px solid var(--line-2);
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 引文前面那个说它是什么的词（「原因」）。 */
.notification-item :deep(.n-label) {
  margin-right: 6px;
  color: var(--muted);
}

.notification-item :deep(.n-emoji) {
  margin-left: 6px;
}
</style>
