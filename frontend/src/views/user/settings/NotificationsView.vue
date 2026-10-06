<script setup lang="ts">
/**
 * 「通知」设置页的渲染面：只吃 props、只发事件（A 档场景）。取数与保存都在
 * 同目录的 `Notifications.vue` 里——它是这一页的容器。
 *
 * 三块，自上而下：渠道（站内 / 浏览器推送 / 邮件），免打扰（安静时段 / 摘要频率），
 * 按类型（八类事件 × 三个渠道的矩阵）。默认值照设计稿：邮件默认「摘要」，安静时段
 * 默认 22:00–08:00，摘要默认「每周」，矩阵里「有人回应了我」默认只进站内。
 *
 * 拨一下开关就提交，所以保存结果（`saving` / `saved` / `error`）摆在页头右上角，
 * 就地留痕（docs/design-system.md §3.11）。
 *
 * 保存中只由 `pending` 指出**动的是哪一处**，那一处自己变忙（开关转圈、圆点变灰），
 * 别处的显示一律不动 —— 整页控件一起变灰会让人以为「整页都在存」。
 */
import type {
  NotificationChangeKey,
  NotificationDigestCadence,
  NotificationEmailMode,
  NotificationEventCategory,
  NotificationEventChannel,
  NotificationField,
  NotificationPreferences,
} from '@/lib/notificationPreferences'

import { computed } from 'vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { t } from '@/i18n'
import {
  CATEGORY_LABEL_KEY,
  EVENT_CHANNEL_LABEL_KEY,
  eventCellKey,
  NOTIFICATION_EVENT_CATEGORIES,
  NOTIFICATION_EVENT_CHANNELS,
} from '@/lib/notificationPreferences'

// 安静时段按这个浏览器的时区算：页面打开时就把它报到账号上（services/account.ts）。
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone

const props = defineProps<{
  prefs: NotificationPreferences | null
  loading: boolean
  failed: boolean
  /** 读失败时服务端那句真实原因，没有就是空。 */
  failingReason: string | null
  saving: boolean
  saved: boolean
  error: string
  /** 正在存的是哪一处（字段名或矩阵格键）；没有就是 null。只有它该显出「在存」。 */
  pending: NotificationChangeKey | null
}>()

const emit = defineEmits<{
  retry: []
  setField: [key: NotificationField, value: string | boolean]
  toggleEvent: [category: NotificationEventCategory, channel: NotificationEventChannel]
}>()

type QuietValue = 'on' | 'off'

// 选项的文字随语言走，所以在 computed 里取，不在模块顶层取一次。
const emailOptions = computed<{ value: NotificationEmailMode; label: string }[]>(() => [
  { value: 'instant', label: t('account.notifications.channels.emailInstant') },
  { value: 'digest', label: t('account.notifications.channels.emailDigest') },
  { value: 'off', label: t('account.notifications.channels.emailOff') },
])

const digestOptions = computed<{ value: NotificationDigestCadence; label: string }[]>(() => [
  { value: 'daily', label: t('account.notifications.quiet.daily') },
  { value: 'weekly', label: t('account.notifications.quiet.weekly') },
  { value: 'off', label: t('account.notifications.quiet.off') },
])

const quietOptions = computed<{ value: QuietValue; label: string }[]>(() => [
  {
    value: 'on',
    label: t('account.notifications.quiet.windowOn', {
      start: props.prefs?.quietHoursStart,
      end: props.prefs?.quietHoursEnd,
    }),
  },
  { value: 'off', label: t('account.notifications.quiet.windowOff') },
])

const quietValue = computed<QuietValue>(() => (props.prefs?.quietHoursEnabled ? 'on' : 'off'))

/** 矩阵里一格的可读名字：切换「X」的 Y。 */
function cellLabel(category: NotificationEventCategory, channel: NotificationEventChannel): string {
  return t('account.notifications.toggleEvent', {
    event: t(CATEGORY_LABEL_KEY[category]),
    channel: t(EVENT_CHANNEL_LABEL_KEY[channel]),
  })
}
</script>

<template>
  <div class="settings-page">
    <header class="notif__head">
      <div>
        <h1 class="t-page-title">{{ t('account.notifications.title') }}</h1>
        <p class="settings-page__lede">{{ t('account.notifications.lede') }}</p>
      </div>
      <SaveStatus
        class="notif__status"
        :saving="saving"
        :saved="saved"
        :error="error"
        :failed-text="t('account.notifications.saveFailed')"
      />
    </header>

    <div v-if="loading" class="settings-card notif__loading">
      <v-progress-circular indeterminate size="22" :aria-label="t('account.notifications.loading')" />
    </div>

    <BaseLoadError
      v-else-if="failed"
      :title="t('account.notifications.loadFailed')"
      :error="failingReason"
      @retry="emit('retry')"
    />

    <template v-else-if="prefs">
      <div class="notif__cols">
        <section class="settings-card">
          <h2 class="settings-card__title">{{ t('account.notifications.channels.title') }}</h2>

          <div class="srow">
            <label class="srow__k" for="notif-in-app">{{ t('account.notifications.channels.inApp') }}</label>
            <span class="srow__v">{{ t('account.notifications.channels.inAppHint') }}</span>
            <v-switch
              id="notif-in-app"
              :model-value="prefs.inAppEnabled"
              :loading="pending === 'inAppEnabled'"
              :disabled="pending === 'inAppEnabled'"
              color="primary"
              density="compact"
              inset
              hide-details
              @update:model-value="(v) => emit('setField', 'inAppEnabled', v === true)"
            />
          </div>

          <div class="srow">
            <label class="srow__k" for="notif-push">{{ t('account.notifications.channels.push') }}</label>
            <span class="srow__v">{{ t('account.notifications.channels.pushHint') }}</span>
            <v-switch
              id="notif-push"
              :model-value="prefs.pushEnabled"
              :loading="pending === 'pushEnabled'"
              :disabled="pending === 'pushEnabled'"
              color="primary"
              density="compact"
              inset
              hide-details
              @update:model-value="(v) => emit('setField', 'pushEnabled', v === true)"
            />
          </div>

          <div class="srow">
            <span class="srow__k">{{ t('account.notifications.channels.email') }}</span>
            <span class="srow__v">{{ t('account.notifications.channels.emailHint') }}</span>
            <SegmentedControl
              :model-value="prefs.emailMode"
              :options="emailOptions"
              :label="t('account.notifications.channels.email')"
              @update:model-value="(v) => emit('setField', 'emailMode', v)"
            />
          </div>
        </section>

        <section class="settings-card">
          <h2 class="settings-card__title">{{ t('account.notifications.quiet.title') }}</h2>

          <div class="srow">
            <span class="srow__k">{{ t('account.notifications.quiet.window') }}</span>
            <span class="srow__v">{{ t('account.notifications.quiet.windowHint', { zone }) }}</span>
            <SegmentedControl
              :model-value="quietValue"
              :options="quietOptions"
              :label="t('account.notifications.quiet.window')"
              @update:model-value="(v) => emit('setField', 'quietHoursEnabled', v === 'on')"
            />
          </div>

          <div class="srow">
            <span class="srow__k">{{ t('account.notifications.quiet.digest') }}</span>
            <span class="srow__v">{{ t('account.notifications.quiet.digestHint') }}</span>
            <SegmentedControl
              :model-value="prefs.digestCadence"
              :options="digestOptions"
              :label="t('account.notifications.quiet.digest')"
              @update:model-value="(v) => emit('setField', 'digestCadence', v)"
            />
          </div>
        </section>
      </div>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.notifications.matrix.title') }}</h2>
        <p class="settings-card__desc">{{ t('account.notifications.matrix.desc') }}</p>

        <table class="notif-matrix">
          <thead>
            <tr>
              <th scope="col" class="notif-matrix__event">{{ t('account.notifications.matrix.event') }}</th>
              <th v-for="channel in NOTIFICATION_EVENT_CHANNELS" :key="channel" scope="col" class="notif-matrix__cell">
                {{ t(EVENT_CHANNEL_LABEL_KEY[channel]) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="category in NOTIFICATION_EVENT_CATEGORIES" :key="category">
              <th scope="row" class="notif-matrix__event">{{ t(CATEGORY_LABEL_KEY[category]) }}</th>
              <td v-for="channel in NOTIFICATION_EVENT_CHANNELS" :key="channel" class="notif-matrix__cell">
                <button
                  type="button"
                  class="notif-dot"
                  :class="{ 'notif-dot--on': prefs.events[category][channel] }"
                  :aria-pressed="prefs.events[category][channel]"
                  :aria-label="cellLabel(category, channel)"
                  :disabled="pending === eventCellKey(category, channel)"
                  @click="emit('toggleEvent', category, channel)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.notif__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}

.notif__loading {
  display: flex;
  padding: 32px;
  justify-content: center;
}

.notif__cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
  /* 这一段是下面那条容器查询的容器。宽度自己撑满——行内尺寸一被包含，宽度就再也
     推不出来（`components/base/SettingsRow` 的同一件事）。 */
  container-type: inline-size;
  width: 100%;
}

/* 卡片并排时每张只有 ~326px，行里那套 180px 的标签列放不下中间那格说明：它被压到
   12px，中文一个字一行，八到十三行。所以并排时的行改用设置行的窄容器排法
   （`components/base/SettingsRow`，判据同为 672）：标签和控件一行，说明另起一行占满。
   660 是上面那条 auto-fit 开始并排的宽度（320×2 + 20），判据是这一栏有多宽——并排与
   否由它决定，窗口宽度答不对。 */
@container (min-width: 660px) {
  .notif__cols .srow {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 4px 16px;
  }

  .notif__cols .srow__v {
    grid-row: 2;
    grid-column: 1 / -1;
  }
}

.notif-matrix {
  width: 100%;
  border-collapse: collapse;
}

.notif-matrix th,
.notif-matrix td {
  padding: 10px 24px;
  border-top: 1px solid var(--line);
}

.notif-matrix thead th {
  padding-top: 2px;
  padding-bottom: 8px;
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  color: var(--muted);
  border-top: 0;
}

.notif-matrix tbody th {
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
}

.notif-matrix__event {
  text-align: left;
}

.notif-matrix__cell {
  width: 80px;
}

.notif-dot {
  width: 18px;
  height: 18px;
  padding: 0;
  cursor: pointer;
  background: transparent;
  border: 1.5px solid var(--line-2);
  border-radius: var(--radius-pill);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}

.notif-dot:hover:not(:disabled) {
  border-color: var(--muted);
}

.notif-dot--on {
  background: var(--ink);
  border-color: var(--ink);
}

.notif-dot:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.notif-dot:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 手机外壳（窄于 768 —— 共享 token，见 `styles/breakpoints.scss`、`settings-card.css`）。 */
@media (max-width: 767.98px) {
  .notif-matrix th,
  .notif-matrix td {
    padding: 10px 16px;
  }
}
</style>
