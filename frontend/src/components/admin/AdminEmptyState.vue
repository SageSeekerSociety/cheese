<script setup lang="ts">
// 后台与反馈页共用的「这里没有东西 / 出错了 / 在加载」块。
//
// 之前有七个名字（`qpage__state`、`ad__none-*`、`am__none`、`fb-empty`、`fb-state`…），
// 文案口径也各说各的。这里只收三样：一句标题、一句为什么、至多一个动作。`tone="error"`
// 只换图标颜色，不整块刷红——错误说清楚就够了，红底会把一张表的空白变成警报。
defineOptions({ name: 'AdminEmptyState' })

withDefaults(
  defineProps<{
    title: string
    desc?: string
    icon?: string
    action?: string
    tone?: 'neutral' | 'error'
    /** 紧凑版：放进卡片或表格里时用，顶距小一档。 */
    compact?: boolean
  }>(),
  { desc: undefined, icon: 'mdi-tray-remove', action: undefined, tone: 'neutral', compact: false },
)

const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="aes" :class="{ 'aes--compact': compact, 'aes--error': tone === 'error' }" role="status">
    <v-icon class="aes__icon" :icon="tone === 'error' ? 'mdi-alert-circle-outline' : icon" size="28" />
    <p class="aes__title">{{ title }}</p>
    <p v-if="desc" class="aes__desc">{{ desc }}</p>
    <button v-if="action" type="button" class="aes__btn" @click="emit('action')">{{ action }}</button>
    <slot />
  </div>
</template>

<style scoped>
.aes {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 360px;
  margin: 0 auto;
  padding: 64px 16px;
  text-align: center;
}

.aes--compact {
  padding: 32px 16px;
}

.aes__icon {
  color: var(--faint);
  margin-bottom: 8px;
}

.aes--error .aes__icon {
  color: var(--danger);
}

.aes__title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.aes__desc {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.aes__btn {
  height: 28px;
  margin-top: 14px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--text);
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.aes__btn:hover {
  background: var(--fill);
}

.aes__btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
