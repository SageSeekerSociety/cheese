<script setup lang="ts">
import type { StatsDays } from '@/lib/adminStats'

import { useI18n } from 'vue-i18n'

// 统计页页头右边那两样：统计窗口 7/30/90 和更新时间戳。标题由页面交给 `AdminPage`。
defineProps<{
  /** 这一类的数有「过去 N 天」这个说法吗（性能读进程内存、集成是存量）。 */
  windowed: boolean
  /** 当前窗口（天）。 */
  days: StatsDays
  /** 「更新于 HH:MM」。 */
  stamp: string
}>()

const emit = defineEmits<{
  /** 换窗口（7/30/90）。 */
  setDays: [days: StatsDays]
}>()

const { t } = useI18n()
</script>

<template>
  <!-- 统计窗口 7/30/90。只有窗口类（`WINDOWED_KINDS`）给这个切换器：
               性能读进程内存、集成是存量，它们没有「过去 N 天」—— 摆着是个假开关。
               切窗口由 `setStatsDays` 把已加载的窗口类全部重拉（缓存键=窗口）。
               形状按 `AdminTabs` 的 `sm` 档（30px 高、12.5px、琥珀下划线），但**没有**
               换成那个组件：e2e 是按 `getByRole('button', { name: '30 天' })` 点它的
               （`AdminTabs` 渲染的是 `role="tab"`），换组件就得连 e2e 一起改。 -->
  <div v-if="windowed" class="ad__wintabs" role="group" :aria-label="t('feedback.dashboard.window.switchAria')">
    <button
      type="button"
      class="ad__wintab"
      :class="{ 'ad__wintab--on': days === 7 }"
      :aria-pressed="days === 7"
      @click="emit('setDays', 7)"
    >
      {{ t('feedback.dashboard.window.d7') }}
    </button>
    <button
      type="button"
      class="ad__wintab"
      :class="{ 'ad__wintab--on': days === 30 }"
      :aria-pressed="days === 30"
      @click="emit('setDays', 30)"
    >
      {{ t('feedback.dashboard.window.d30') }}
    </button>
    <button
      type="button"
      class="ad__wintab"
      :class="{ 'ad__wintab--on': days === 90 }"
      :aria-pressed="days === 90"
      @click="emit('setDays', 90)"
    >
      {{ t('feedback.dashboard.window.d90') }}
    </button>
  </div>
  <span class="ad__stamp t-meta-read">{{ stamp }}</span>
</template>

<style scoped>
/* 页头那一行（标题 / 窗口切换 / 时间戳）里最不重要的一个：窄屏让位（截断），
   不把页头撑出横向滚动。 */

.ad__stamp {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 切换控件只有一种语言：下划线小页签。窗口切换是小号版（12.5px），分类页签是
   正文版（13.5px）；激活 = 墨色 + 2px 下划线，未激活 = 灰。整页没有框状切换钮。 */

.ad__wintabs {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 4px;
}

/* 尺子抄 `AdminTabs` 的 `sm` 档：30px 高、12.5px、左右 8px 内边距、选中的那条
   2px 下划线压在容器下沿。整页的切换控件因此只有一种尺寸语言。 */

.ad__wintab {
  position: relative;
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  background: none;
  border: 0;
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
}

.ad__wintab::after {
  position: absolute;
  right: 8px;
  bottom: 0;
  left: 8px;
  height: 2px;
  background: var(--ink);
  opacity: 0;
  content: '';
}

.ad__wintab--on {
  color: var(--ink);
  font-weight: 600;
}

.ad__wintab--on::after {
  opacity: 1;
}

@media (hover: hover) and (pointer: fine) {
  .ad__wintab:not(.ad__wintab--on):hover {
    color: var(--text);
  }
}

.ad__wintab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
