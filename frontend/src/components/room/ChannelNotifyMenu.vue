<script setup lang="ts">
// 频道顶栏的铃铛：我对这个频道的通知档位。三档——所有新消息、只在 @我和我参与的
// 支线有回复时（默认）、静音——静音还能选多久。数字和通知怎么跟着档位走是后端的
// 事（`TopicRepository.unread_counts`），这里只把选择交出去。
import type { TopicNotifyLevel } from '@/types/channels'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  level: TopicNotifyLevel
  /** 静音到什么时候（ISO）；null = 直到我取消，或者没在静音。 */
  mutedUntil: string | null
}>()

const emit = defineEmits<{
  (e: 'set', level: TopicNotifyLevel, until: string | null): void
}>()

const LEVELS: { level: Exclude<TopicNotifyLevel, 'mute'>; label: string; hint: string }[] = [
  { level: 'all', label: 'work.channel.notify.all', hint: 'work.channel.notify.allHint' },
  { level: 'mentions', label: 'work.channel.notify.mentions', hint: 'work.channel.notify.mentionsHint' },
]

/** 静音多久：到一个时刻，或 null（直到我取消）。时刻在点下去那一刻才算。 */
const DURATIONS: { key: string; label: string; until: () => string | null }[] = [
  { key: 'hour', label: 'work.channel.notify.forHour', until: () => later(60 * 60 * 1000) },
  { key: 'eight', label: 'work.channel.notify.forEightHours', until: () => later(8 * 60 * 60 * 1000) },
  { key: 'tomorrow', label: 'work.channel.notify.untilTomorrow', until: tomorrowMorning },
  { key: 'week', label: 'work.channel.notify.forWeek', until: () => later(7 * 24 * 60 * 60 * 1000) },
  { key: 'forever', label: 'work.channel.notify.untilUnmuted', until: () => null },
]

function later(ms: number): string {
  return new Date(Date.now() + ms).toISOString()
}

/** 明天早上 9 点（本地时间）。 */
function tomorrowMorning(): string {
  const at = new Date()
  at.setDate(at.getDate() + 1)
  at.setHours(9, 0, 0, 0)
  return at.toISOString()
}

const icon = computed(() =>
  props.level === 'mute' ? 'mdi-bell-off-outline' : props.level === 'all' ? 'mdi-bell-ring-outline' : 'mdi-bell-outline'
)

const mutedLine = computed(() => {
  if (props.level !== 'mute') return ''
  if (!props.mutedUntil) return t('work.channel.notify.mutedForever')
  const at = new Date(props.mutedUntil)
  return t('work.channel.notify.mutedUntil', {
    time: at.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
  })
})
</script>

<template>
  <v-menu location="bottom end" :close-on-content-click="true">
    <template #activator="{ props: menuProps }">
      <BaseButton
        v-bind="menuProps"
        :icon="icon"
        size="sm"
        class="tap-target"
        :title="t('work.channel.notify.title')"
        :aria-label="t('work.channel.notify.title')"
      />
    </template>
    <v-card min-width="280" class="notify-menu" role="menu">
      <div class="notify-menu__head t-eyebrow">{{ t('work.channel.notify.title') }}</div>
      <button
        v-for="option in LEVELS"
        :key="option.level"
        type="button"
        role="menuitemradio"
        :aria-checked="level === option.level"
        class="notify-menu__row"
        @click="emit('set', option.level, null)"
      >
        <v-icon size="16" class="notify-menu__check">{{ level === option.level ? 'mdi-check' : '' }}</v-icon>
        <span class="notify-menu__text">
          <span>{{ t(option.label) }}</span>
          <span class="notify-menu__hint">{{ t(option.hint) }}</span>
        </span>
      </button>
      <div class="notify-menu__rule" />
      <div class="notify-menu__row notify-menu__row--static">
        <v-icon size="16" class="notify-menu__check">{{ level === 'mute' ? 'mdi-check' : '' }}</v-icon>
        <span class="notify-menu__text">
          <span>{{ t('work.channel.notify.mute') }}</span>
          <span class="notify-menu__hint">{{ mutedLine || t('work.channel.notify.muteHint') }}</span>
        </span>
      </div>
      <button
        v-for="option in DURATIONS"
        :key="option.key"
        type="button"
        role="menuitem"
        class="notify-menu__row notify-menu__row--sub"
        @click="emit('set', 'mute', option.until())"
      >
        {{ t(option.label) }}
      </button>
    </v-card>
  </v-menu>
</template>

<style scoped>
.notify-menu {
  padding-block: 4px;
}
.notify-menu__head {
  padding: 8px 16px 4px;
  color: var(--muted);
}
.notify-menu__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  min-height: 36px;
  padding: 6px 16px;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.notify-menu__row:hover {
  background: var(--fill);
}
.notify-menu__row--static {
  cursor: default;
}
.notify-menu__row--static:hover {
  background: transparent;
}
.notify-menu__row--sub {
  align-items: center;
  padding-left: 40px;
  color: var(--muted);
}
.notify-menu__check {
  flex: 0 0 16px;
  margin-top: 2px;
}
.notify-menu__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.notify-menu__hint {
  color: var(--faint);
  font-size: 12px;
}
.notify-menu__rule {
  margin: 4px 0;
  border-top: 1px solid var(--line);
}
</style>
