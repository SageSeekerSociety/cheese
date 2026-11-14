<script setup lang="ts">
// 频道里审阅一件任务时的页头：任务名、第几版、「退回」「采纳」，再是「打开任务」。审阅人
// 已经决定了的，按钮的位置写决定的结果（已采纳 / 已退回）。桌面上画在交付那一格顶上；
// 手机上那是一整页，这一行交给顶栏（TopicView 用 useScreenHeader），只有任务名和「打开
// 任务」，决定在页面底部那一条上。
import { vGuideAnchor } from '@/composables/useGuideAnchors'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'DeliveryHead' })

withDefaults(
  defineProps<{
    /** 任务名；还没读回来时是空的。 */
    heading: string
    /** 这一版是第几版；不知道时不写。 */
    version?: number | null
    /** 这一版已经决定了：写结果，不再给按钮。 */
    decided?: 'accepted' | 'returned' | null
    /** 轮到看的人决定：给「退回」「采纳」。 */
    decide?: boolean
    acceptLabel?: string
    /** 采纳为什么现在点不了（按钮的 title），空表示可以。 */
    blockedTitle?: string | null
    busy?: boolean
    /** 画在手机顶栏里：顶栏自己有高度和底线，这一行不再带。 */
    bar?: boolean
  }>(),
  { version: null, decided: null, decide: false, acceptLabel: '', blockedTitle: null, busy: false, bar: false }
)

const emit = defineEmits<{
  (e: 'open-task'): void
  (e: 'accept'): void
  (e: 'reject'): void
}>()
</script>

<template>
  <header class="delivery-head" :class="{ 'delivery-head--bar': bar }">
    <h2 class="delivery-head__title t-title" :title="heading">{{ heading }}</h2>
    <span v-if="version" class="delivery-head__version t-meta">{{
      t('work.room.delivery.version', { n: version })
    }}</span>
    <span class="delivery-head__gap" />
    <span v-if="decided" class="delivery-head__decided t-meta" data-testid="delivery-decided">
      <v-icon v-if="decided === 'accepted'" size="16" class="delivery-head__ok" aria-hidden="true"
        >mdi-check-circle-outline</v-icon
      >
      {{ decided === 'accepted' ? t('work.room.accept.accepted') : t('work.room.delivery.returned') }}
    </span>
    <template v-else-if="decide">
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-undo" :disabled="busy" @click="emit('reject')">
        {{ t('work.room.accept.sendBack') }}
      </BaseButton>
      <span :title="blockedTitle ?? undefined">
        <BaseButton
          kind="primary"
          size="sm"
          prepend-icon="mdi-check"
          :loading="busy"
          :disabled="busy || !!blockedTitle"
          @click="emit('accept')"
        >
          {{ acceptLabel }}
        </BaseButton>
      </span>
    </template>
    <BaseButton
      v-guide-anchor="'review-open-task'"
      kind="secondary"
      size="sm"
      data-testid="delivery-open-task"
      @click="emit('open-task')"
      >{{ t('work.room.delivery.openTask') }}</BaseButton
    >
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
.delivery-head__version {
  flex: none;
  color: var(--muted);
}
.delivery-head__gap {
  flex: 1 1 0;
}
.delivery-head__decided {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}
.delivery-head__ok {
  color: var(--ok);
}
</style>
