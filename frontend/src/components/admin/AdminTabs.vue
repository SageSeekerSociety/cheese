<script setup lang="ts" generic="T extends string">
// 后台的一排页签：下划线式，选中项 --ink + 2px 琥珀下划线。
//
// 看板的分类页签、看板与模型页的时间窗口、队列的状态页签、反馈中心的状态筛选，之前
// 分别是自写下划线、`v-btn-toggle`、裸药丸三种样子。统一成这一种。放不下时整排横向
// 滚动（不折行、不裁字），右缘给一道渐隐提示「后面还有」。
import { vRovingTabs } from '@/lib/rovingTabs'

defineOptions({ name: 'AdminTabs' })

defineProps<{
  modelValue: T
  options: ReadonlyArray<{ value: T; label: string; count?: number | null }>
  label: string
  /** `sm` 用在一行里挤着别的控件的地方（时间窗口）。 */
  size?: 'md' | 'sm'
  /** 这排页签切换的那块内容（`role="tabpanel"`）的 id；给了就写进每一格的 `aria-controls`。 */
  controls?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
</script>

<template>
  <div class="atabs" :class="size === 'sm' ? 'atabs--sm' : ''">
    <div v-roving-tabs class="atabs__track" role="tablist" :aria-label="label">
      <button
        v-for="o in options"
        :key="o.value"
        type="button"
        role="tab"
        class="atabs__tab"
        :class="{ 'atabs__tab--on': o.value === modelValue }"
        :aria-selected="o.value === modelValue"
        :aria-controls="controls"
        @click="emit('update:modelValue', o.value)"
      >
        {{ o.label }}
        <span v-if="o.count != null" class="atabs__count">{{ o.count }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.atabs {
  position: relative;
  min-width: 0;
}

.atabs__track {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
  mask-image: linear-gradient(to right, var(--ink) calc(100% - 24px), transparent);
}

.atabs__track::-webkit-scrollbar {
  display: none;
}

.atabs__tab {
  position: relative;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 10px;
  background: transparent;
  border: 0;
  color: var(--muted);
  font: inherit;
  font-size: 13.5px;
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}

.atabs__tab:last-child {
  margin-right: 20px;
}

.atabs__tab:hover {
  color: var(--ink);
}

.atabs__tab--on {
  color: var(--ink);
  font-weight: 600;
}

.atabs__tab--on::after {
  content: '';
  position: absolute;
  right: 10px;
  bottom: 0;
  left: 10px;
  height: 2px;
  background: var(--accent);
}

.atabs__tab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
  border-radius: var(--radius-sm);
}

.atabs__count {
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.atabs--sm .atabs__tab {
  height: 30px;
  padding: 0 8px;
  font-size: 12.5px;
}

.atabs--sm .atabs__tab--on::after {
  right: 8px;
  left: 8px;
}
</style>
