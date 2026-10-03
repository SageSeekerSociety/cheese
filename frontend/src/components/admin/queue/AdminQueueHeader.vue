<script setup lang="ts">
import type { QueueView } from '@/composables/useAdminQueue'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

// 队列页页头右边那排工具 —— 未读徽标、「标记为已读」、刷新、视图切换（F-05）。标题和
// 说明由页面交给 `AdminPage`。
//
// 这一件不认识接口、不认识路由、不认识 store：吃 props、往上发事件。**未读数是一个 prop
// 而不是它自己去问 store** —— 「有没有未读」是页面（更准确地说，是 store 那份 counts）
// 的判断，页头只负责在大于 0 的时候画出来。
//
// 视图切换**不重新取数**：两个视图读的是同一份 `adminItems`（§13 C-10），所以这里只报
// 「人点了哪一档」，换数据那件事不在这条路上。切换器画成 24px 的分段控件、落在页头右上
// 角，和工具行里那两组（栏位 / 状态页签）刻意长得不一样 —— 它换的是「怎么看」，那两组
// 换的是「看哪些」。
defineOptions({ name: 'AdminQueueHeader' })

defineProps<{
  /** 未读条数。`0` 时徽标和那颗按钮整个不画 —— 「0 未读」是一句噪音。 */
  unread: number
  /** 当前那一档。 */
  view: QueueView
}>()

const emit = defineEmits<{
  'mark-read': []
  refresh: []
  'update:view': [view: QueueView]
}>()

const { t } = useI18n()

/** 分段控件的两个档。写成常量是因为模板里那一段要在 `v-for` 上判等。 */
const VIEWS: QueueView[] = ['list', 'table']
</script>

<template>
  <!-- 未读数。F-13 修的就是它：这个数以前没有人清零，也没有一处模板读它。 -->
  <span v-if="unread > 0" class="qpage__badge t-num" aria-live="polite">
    {{ t('feedback.queue.unread', { n: unread }) }}
  </span>
  <BaseButton
    v-if="unread > 0"
    kind="ghost"
    size="sm"
    :title="t('notifications.common.markAsRead')"
    @click="emit('mark-read')"
  >
    {{ t('notifications.common.markAsRead') }}
  </BaseButton>

  <button
    type="button"
    class="qpage__icon-btn"
    :aria-label="t('feedback.queue.refresh')"
    :title="t('feedback.queue.refresh')"
    @click="emit('refresh')"
  >
    <v-icon icon="mdi-refresh" size="16" aria-hidden="true" />
  </button>

  <!-- 视图切换（F-05）：24px 高，落在页头右上角。**切换不重新取数** —— 两个视图
           读的是同一份 `adminItems`（§13 C-10）。 -->
  <div class="qpage__seg" role="group" :aria-label="t('feedback.queue.label')">
    <button
      v-for="option in VIEWS"
      :key="option"
      type="button"
      class="qpage__seg-btn"
      :class="{ 'qpage__seg-btn--on': view === option }"
      :aria-pressed="view === option"
      @click="emit('update:view', option)"
    >
      {{ option === 'list' ? t('feedback.queue.view.list') : t('feedback.queue.view.table') }}
    </button>
  </div>
</template>

<style scoped>
.qpage__badge {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.qpage__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__icon-btn:hover {
  background: var(--fill);
  color: var(--text);
}

/* 分段控件：24px 高的轨道（§4.1 的 24px 是铁的），滑块**占满整个高度**，两端各留
   4px。早先写的是「24px 轨道 + 20px 滑块」，那让轨道上下各多出 2px 内边距 —— 2 不在
   间距的尺子上（§15 第 27 条）。滑块顶到边之后，那 2px 也就不存在了。
   选中态是**中性**的（`--surface` 底 + 1px `--line`），不是琥珀 —— 队列是全站唯一一处
   amber 数等于 0 的视图（§7.4）。 */
.qpage__seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 4px;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qpage__seg-btn {
  height: 24px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid transparent;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__seg-btn--on {
  background: var(--surface);
  border-color: var(--line);
  color: var(--ink);
}
</style>
