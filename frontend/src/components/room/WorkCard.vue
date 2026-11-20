<script setup lang="ts">
// AI 队友交出来的一样东西（新建的文档、提议的技能……）：一行小卡片，和对话里的文件
// 小卡片一个样子——图标、名字、是什么。点它在右侧打开，读全文、做决定都在那边；卡上
// 只说它现在怎样（待确认、谁已保存），不铺正文。
import { t } from '@/i18n'

defineProps<{
  /** 卡片左边那枚图标（mdi 名）。 */
  icon: string
  /** 这样东西叫什么。 */
  title: string
  /** 它是什么：文档、提议新增技能…… */
  kind: string
  /** 它现在怎样：待确认、某某已保存。没有就不写。 */
  state?: string | null
  /** 还在等人决定：状态字用提醒色。 */
  pending?: boolean
}>()

const emit = defineEmits<{
  (e: 'open'): void
}>()
</script>

<template>
  <div class="work-card" data-testid="work-card">
    <button
      type="button"
      class="work-card__main"
      :title="t('work.room.workCard.open', { name: title })"
      @click="emit('open')"
    >
      <span class="work-card__face" aria-hidden="true">
        <v-icon size="18">{{ icon }}</v-icon>
      </span>
      <span class="work-card__text">
        <span class="work-card__title">{{ title }}</span>
        <span class="work-card__kind">{{ kind }}</span>
      </span>
      <span v-if="state" class="work-card__state" :class="{ 'work-card__state--pending': pending }">{{ state }}</span>
      <v-icon class="work-card__go" size="16" aria-hidden="true">mdi-dock-right</v-icon>
    </button>
  </div>
</template>

<style scoped>
/* 一行，只有内容那么宽：拉满整行就不像一样东西，像一条横幅。 */
.work-card {
  display: inline-flex;
  align-items: center;
  min-width: min(320px, 100%);
  max-width: min(480px, 100%);
  margin-top: 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.work-card:hover {
  border-color: var(--faint);
}
.work-card__main {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 8px;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.work-card__face {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.work-card__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}
.work-card__title {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-card__kind,
.work-card__state {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.work-card__state {
  flex: none;
}
.work-card__state--pending {
  color: var(--warn-ink);
}
.work-card__go {
  flex: none;
  color: var(--faint);
}
</style>
