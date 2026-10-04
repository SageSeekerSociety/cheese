<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseButton from '@/components/base/BaseButton.vue'

// 模型管理页页头右边那排工具：网关健康灯、窗口页签、刷新。标题和说明（带当前窗口）由
// 页面交给 `AdminPage`。
//
// 窗口是**三段共用**的，所以它摆在这里（页面级），不塞进某一段的工具条里；健康灯同理，
// 它说的是这一页读的那一个网关活没活，属于页头不属于任何一段。
//
// 这一件不认识接口、不认识路由：吃 props、往上发事件。窗口页签那三档的**文案**在这里
// 拼（它要 `t`），档位本身由页面给（`windows`）——「有哪几档」是取数那一边的事。
const props = defineProps<{
  /** 网关健康灯；`null` = 还没读到（不画 —— 「还没读到」不是一种健康状态）。 */
  health: { ok: boolean; text: string; title: string } | null
  /** 当前窗口（天）。 */
  days: number
  /** 可选窗口档位。三段共用，所以由页面给。 */
  windows: number[]
  /** 主列表在加载中（刷新按钮转圈）。 */
  loading: boolean
}>()

const emit = defineEmits<{ 'change-window': [days: number]; refresh: [] }>()

const { t } = useI18n()

/** 窗口页签。值走字符串 —— `AdminTabs` 是 `T extends string` 的泛型；发出去的这一刻
 *  就收成数字：`days` 是发给接口的参数，别让它变成字符串。 */
const options = computed(() => props.windows.map((n) => ({ value: String(n), label: t('models.page.days', { n }) })))
</script>

<template>
  <!-- readiness 健康灯：常在的一眼状态，点与文字，完整 detail 挂 title。
           触屏没有 hover —— 读不出来时表格里那条说明会给出原因（和这里的
           `health` 同一份判据），灯坏了的人不至于只能盯着一个小点猜。 -->
  <span v-if="health" class="amd__health" role="status" :aria-label="t('models.health.label')" :title="health.title">
    <span class="amd__healthdot" :class="health.ok ? 'amd__dot--ok' : 'amd__dot--danger'" aria-hidden="true" />
    <span class="amd__healthtext t-meta-read">{{ health.text }}</span>
  </span>
  <!-- 窗口是三段共用的，所以它摆在页头（页面级），不塞进某一段的工具条里。 -->
  <AdminTabs
    size="sm"
    :label="t('models.page.window')"
    :model-value="String(days)"
    :options="options"
    @update:model-value="emit('change-window', Number($event))"
  />
  <BaseButton
    icon="mdi-refresh"
    size="sm"
    :aria-label="t('models.page.refresh')"
    :loading="loading"
    @click="emit('refresh')"
  />
</template>

<style scoped>
/* 页头健康灯：8px 圆点 + 一句状态。它是「常在」的那一眼，细节在 title 与
   gatewayDown 警告条上。 */
.amd__health {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.amd__healthdot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}

.amd__healthtext {
  overflow: hidden;
  max-width: 260px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 网关健康灯的色调。 */
.amd__dot--ok {
  background: var(--ok);
}

.amd__dot--danger {
  background: var(--danger);
}
</style>
