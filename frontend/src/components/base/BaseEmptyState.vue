<script setup lang="ts">
/**
 * 「这里没有东西」的那一块（docs/design-system.md §3.11，文案口径见 §8.1）。
 *
 * 之前空态各页手写：`settings-empty`、`an-note`、`rs__note`、`empty-panel`、Vuetify 的
 * `text-center py-12` 卡片……同一件事十几种长相。这里只收三样：一句标题、一句为什么、
 * 至多一个动作；长相由 `size` 决定，不由调用处的 class 决定。
 *
 * - `page`    整块区域就是空的：图标 + 标题 + 说明 + 动作，居中，上下留白大。
 * - `compact` 放进卡片、表格、抽屉里：同样的四件，留白小一档。
 * - `inline`  一行灰字，没有图标：设置卡片里、列表下面、筛选后的一句「暂无 X」。
 *             外边距由所在的那块决定（例如 `.settings-empty` 只管内距），这里不加。
 *
 * `tone="error"` 只换图标颜色，不整块刷红。读失败不该用空态，见 §3.10 的
 * `BaseLoadError`；这里的 error 只给「筛选条件无效」这类本身就是空的情形。
 *
 * 标题之外还要放东西（链接、一段带格式的话）用默认插槽，它排在说明和动作之后。
 */
import { computed } from 'vue'

import BaseButton from './BaseButton.vue'

export type EmptyStateSize = 'page' | 'compact' | 'inline'

const props = withDefaults(
  defineProps<{
    title?: string
    desc?: string
    icon?: string
    action?: string
    tone?: 'neutral' | 'error'
    size?: EmptyStateSize
    /** `inline` 默认靠左，另外两档默认居中。 */
    align?: 'center' | 'start'
  }>(),
  {
    title: undefined,
    desc: undefined,
    icon: 'mdi-tray-remove',
    action: undefined,
    tone: 'neutral',
    size: 'page',
    align: undefined,
  }
)

const emit = defineEmits<{ action: [] }>()

const resolvedAlign = computed(() => props.align ?? (props.size === 'inline' ? 'start' : 'center'))
const showIcon = computed(() => props.size !== 'inline')
</script>

<template>
  <div class="bes" :class="[`bes--${size}`, `bes--${resolvedAlign}`, { 'bes--error': tone === 'error' }]" role="status">
    <v-icon v-if="showIcon" class="bes__icon" :icon="tone === 'error' ? 'mdi-alert-circle-outline' : icon" size="28" />
    <p v-if="title" class="bes__title">{{ title }}</p>
    <p v-if="desc" class="bes__desc">{{ desc }}</p>
    <BaseButton v-if="action" kind="secondary" size="sm" class="bes__btn" @click="emit('action')">
      {{ action }}
    </BaseButton>
    <slot />
  </div>
</template>

<style scoped>
.bes {
  display: flex;
  flex-direction: column;
}

.bes--center {
  align-items: center;
  margin: 0 auto;
  text-align: center;
}

.bes--start {
  align-items: flex-start;
  text-align: start;
}

.bes--page {
  max-width: 360px;
  padding: 64px 16px;
}

.bes--compact {
  max-width: 360px;
  padding: 32px 16px;
}

.bes--page.bes--start,
.bes--compact.bes--start {
  max-width: none;
}

.bes__icon {
  color: var(--faint);
  margin-bottom: 8px;
}

.bes--error .bes__icon {
  color: var(--danger);
}

.bes__title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.bes__desc {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.bes__btn {
  margin-top: 14px;
}

/* inline: one grey line, same weight as the description so it never reads as a heading. */
.bes--inline .bes__title {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}

.bes--inline .bes__desc {
  margin-top: 2px;
}

.bes--inline .bes__btn {
  margin-top: 8px;
}
</style>
