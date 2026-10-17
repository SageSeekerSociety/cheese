<script setup lang="ts">
// 「还能做什么」在桌面上的那份清单：贴在面板右下角，头上写着对的是哪一条结果。
//
// 清单里的事做的是哪一条由父级拿着（`acting`），这里只画和报选择。手机上这一份换成从
// 底部升起的 `MobileActionSheet`，两条路喂的是同一份清单。
import type { MenuCommand } from '@/commands'

import { actionId } from './ids'

defineProps<{
  /** 这份清单是对哪一条结果上按出来的。 */
  title: string
  /** 那位结果能做的全部事，回车那一件在最前。 */
  actions: MenuCommand[]
  /** 现在停在第几件上。 */
  selected: number
}>()

const emit = defineEmits<{
  /** 鼠标划过一件。 */
  select: [index: number]
  /** 做这一件。 */
  run: [action: MenuCommand]
}>()
</script>

<template>
  <div class="palette__actions" role="menu" :aria-label="title" @mousedown.prevent>
    <div class="palette__actions-head">{{ title }}</div>
    <div
      v-for="(action, i) in actions"
      :id="actionId(i)"
      :key="action.id"
      class="palette__action"
      :class="{ 'palette__action--selected': i === selected, 'palette__action--danger': action.danger }"
      role="menuitem"
      @mousemove="emit('select', i)"
      @click="emit('run', action)"
    >
      <v-icon :icon="action.icon" size="16" class="palette__icon" />
      <span class="palette__title">{{ action.title }}</span>
    </div>
  </div>
</template>

<style scoped>
.palette__actions {
  position: absolute;
  right: 10px;
  bottom: 44px;
  width: 260px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.palette__actions-head {
  overflow: hidden;
  padding: 6px 8px 4px;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.palette__action {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  color: var(--text);
  cursor: pointer;
}
.palette__action--selected {
  background: var(--fill-2);
  color: var(--ink);
}
.palette__action--danger {
  color: var(--danger-ink);
}
.palette__icon {
  flex: none;
  color: var(--muted);
}
.palette__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
