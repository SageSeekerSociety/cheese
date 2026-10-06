<script setup lang="ts">
/**
 * 「通知」（`/users/settings/notifications`）。取数、保存、推送订阅都在这里，
 * 画法在同目录的 `NotificationsView.vue`（A 档场景）——这一页只做它的容器。
 *
 * 一条偏好改一下就提交（设计稿：改动即时保存）。拨浏览器推送那颗开关时会真正
 * 订阅 / 退订这台电脑（`services/webPush` 的 `enablePush` / `disablePush`），其余
 * 字段只是把整份偏好 PUT 回去。
 *
 * 保存一次只跑一个，正落地时又点的那一处排队等它，不丢；而「正在存的是哪一处」
 * 由 `pending` 交给渲染面 —— 只那一处变忙，别处的显示不动。
 */
import type {
  NotificationChangeKey,
  NotificationEventCategory,
  NotificationEventChannel,
  NotificationField,
  NotificationPreferences,
} from '@/lib/notificationPreferences'

import { onMounted, ref } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import NotificationsView from './NotificationsView.vue'

import { getNotificationPreferences, saveNotificationPreferences } from '@/api/notificationPreferences'
import { t } from '@/i18n'
import { eventCellKey } from '@/lib/notificationPreferences'
import { disablePush, enablePush } from '@/services/webPush'

const prefs = ref<NotificationPreferences | null>(null)
const loading = ref(true)
const failed = ref(false)
const failingReason = ref<string | null>(null)

/** 正在存的是哪一处（字段名或矩阵格键）。只让那一处显出「在存」，不整页一起变灰。 */
const pending = ref<NotificationChangeKey | null>(null)

// 拨一下开关就提交，所以结果就地留在页头（§3.11）——一次写用 `run` 包住。
const { saving, saved, error, run } = useSaveState({
  feedback: 'inline',
  messages: { failed: t('account.notifications.saveFailed') },
  describeError: () => t('account.notifications.saveFailed'),
})

/** 上一次保存，还没落地时非空。 */
let inFlight: Promise<unknown> | null = null

/**
 * 保存一次只跑一个（`useSaveState` 的约定）。正落地时点的那一下等它落地，再读一次
 * 当前值发出去——把这一下丢掉就成了「点了没反应」。
 */
async function settled() {
  while (inFlight) await inFlight
}

async function load() {
  loading.value = true
  failed.value = false
  failingReason.value = null
  try {
    prefs.value = await getNotificationPreferences()
  } catch (e) {
    failed.value = true
    failingReason.value = e instanceof Error ? e.message : null
  } finally {
    loading.value = false
  }
}

onMounted(load)

/** 先按新值画出来，保存失败再放回原值——放回也是一次该被重画的变化。 */
async function persist(key: NotificationChangeKey, next: NotificationPreferences, previous: NotificationPreferences) {
  prefs.value = next
  pending.value = key
  const done = run(async () => {
    try {
      prefs.value = await saveNotificationPreferences(next)
    } catch (e) {
      prefs.value = previous
      throw e
    }
  })
  inFlight = done
  try {
    await done
  } finally {
    if (inFlight === done) inFlight = null
    pending.value = null
  }
}

async function setField(key: NotificationField, value: string | boolean) {
  // 关掉推送＝退订这台电脑；打开＝问权限、订阅、把订阅交给后端。两件都在用户点
  // 开关的这一下做（浏览器只在一次手势里才肯弹权限框），所以排在队列前面。
  if (key === 'pushEnabled') {
    if (value === true) await enablePush()
    else await disablePush()
  }
  await settled()
  const current = prefs.value
  if (!current) return
  await persist(key, { ...current, [key]: value } as NotificationPreferences, current)
}

async function toggleEvent(category: NotificationEventCategory, channel: NotificationEventChannel) {
  await settled()
  const current = prefs.value
  if (!current) return
  const choice = current.events[category]
  const next: NotificationPreferences = {
    ...current,
    events: { ...current.events, [category]: { ...choice, [channel]: !choice[channel] } },
  }
  await persist(eventCellKey(category, channel), next, current)
}
</script>

<template>
  <NotificationsView
    :prefs="prefs"
    :loading="loading"
    :failed="failed"
    :failing-reason="failingReason"
    :saving="saving"
    :saved="saved"
    :error="error"
    :pending="pending"
    @retry="load"
    @set-field="setField"
    @toggle-event="toggleEvent"
  />
</template>
