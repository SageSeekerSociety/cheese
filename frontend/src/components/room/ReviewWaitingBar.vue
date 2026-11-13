<script setup lang="ts">
// 频道输入框上方那一条「待你审阅」：这个频道里有任务交了一版、等的是我。写最早的那一件，
// 点它在频道右边审阅；不止一件时右边是件数，点开列出每一件。等的不是我、或者没有等审阅的，
// 这一条不在。
import { t } from '@/i18n'

export interface WaitingReview {
  taskId: string
  title: string
}

const props = defineProps<{
  /** 等我审阅的任务，最早交上来的在前。 */
  items: WaitingReview[]
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()
</script>

<template>
  <div v-if="props.items.length" class="review-waiting" data-testid="review-waiting">
    <span class="review-waiting__label">{{ t('work.room.delivery.waiting') }}</span>
    <button type="button" class="review-waiting__first" @click="emit('open', props.items[0].taskId)">
      {{ props.items[0].title }}
    </button>
    <v-menu v-if="props.items.length > 1" location="top end">
      <template #activator="{ props: menu }">
        <button type="button" class="review-waiting__count" v-bind="menu" data-testid="review-waiting-count">
          {{ t('work.room.delivery.count', { n: props.items.length }) }}
          <v-icon size="16" aria-hidden="true">mdi-chevron-up</v-icon>
        </button>
      </template>
      <v-list density="compact" class="review-waiting__menu">
        <v-list-item
          v-for="item in props.items"
          :key="item.taskId"
          :title="item.title"
          @click="emit('open', item.taskId)"
        />
      </v-list>
    </v-menu>
  </div>
</template>

<style scoped>
/* 和贴在输入框上的采纳那一条同一个位置、同一个外框（TopicAcceptCard 的 .accept-dock）。 */
.review-waiting {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin: 0 12px 8px;
  padding: 6px 8px 6px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  font-size: 14px;
  line-height: var(--lh-14);
}
.review-waiting__label {
  flex: none;
  color: var(--accent-ink);
  font-weight: 600;
}
.review-waiting__first {
  min-width: 0;
  padding: 2px 6px;
  overflow: hidden;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--ink);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.review-waiting__first:hover,
.review-waiting__count:hover {
  background: var(--fill);
}
.review-waiting__count {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  padding: 2px 6px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
</style>
