<script setup lang="ts">
// 频道右边审阅一件任务时的页头：任务名，右边「打开任务」。桌面上画在那一栏顶上，带关闭
// 键；手机上那是一整页，这一行交给顶栏（TopicView 用 useScreenHeader），返回在顶栏左边。
import { vGuideAnchor } from '@/composables/useGuideAnchors'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'DeliveryHead' })

defineProps<{
  /** 任务名；还没读回来时是空的。 */
  heading: string
  /** 桌面那一栏有关闭键；手机上返回在顶栏左边。 */
  closable?: boolean
  /** 画在手机顶栏里：顶栏自己有高度和底线，这一行不再带。 */
  bar?: boolean
}>()

const emit = defineEmits<{
  (e: 'open-task'): void
  (e: 'close'): void
}>()
</script>

<template>
  <header class="delivery-head" :class="{ 'delivery-head--bar': bar }">
    <h2 class="delivery-head__title t-title" :title="heading">{{ heading }}</h2>
    <span class="delivery-head__gap" />
    <BaseButton
      v-guide-anchor="'review-open-task'"
      kind="secondary"
      size="sm"
      data-testid="delivery-open-task"
      @click="emit('open-task')"
      >{{ t('work.room.delivery.openTask') }}</BaseButton
    >
    <BaseButton
      v-if="closable"
      icon="mdi-close"
      size="sm"
      :title="t('work.room.delivery.close')"
      :aria-label="t('work.room.delivery.close')"
      @click="emit('close')"
    />
  </header>
</template>

<style scoped>
.delivery-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--line);
}
/* 顶栏里：高度、底线、左右的边距都是顶栏的。 */
.delivery-head--bar {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  min-height: 0;
  padding: 0 4px 0 0;
  border-bottom: 0;
}
.delivery-head__title {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.delivery-head__gap {
  flex: 1 1 0;
}
</style>
