<script setup lang="ts">
// 频道清单底下那一行：「全部频道」。它和频道行同一条左缘（8px 起 + 16px 的槽），字淡
// 一档：它是个入口，不是一个频道。
//
// 「全部频道」是侧栏只列我加入了的频道时，别的频道从哪儿找——去项目设置的「频道」一栏，
// 看全部频道、加入或退出。原先它下面还有一行「进行中的任务 N」，2026-10-13 撤掉了
// （.claude/rules/project-sidebar.md）；数字和悬停说明这两件能力留着，是因为别处也在
// 用同一颗按钮（比如以后想再挂一行带数的入口）。
defineProps<{
  label: string
  icon: string
  selected: boolean
  /** 行右端的数字；不给就不画。 */
  count?: number
  /** 悬停那一句：这个数是怎么数的。 */
  hint?: string
}>()
const emit = defineEmits<{ (e: 'select'): void }>()
</script>

<template>
  <button
    type="button"
    class="rail-browse"
    :class="{ 'rail-browse--selected': selected }"
    :aria-current="selected ? 'page' : undefined"
    :title="hint || undefined"
    @click="emit('select')"
  >
    <v-icon size="16" class="rail-browse__glyph" :icon="icon" />
    <span>{{ label }}</span>
    <span v-if="count !== undefined" class="rail-browse__count">{{ count }}</span>
  </button>
</template>

<style scoped>
.rail-browse {
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% - 16px);
  min-height: 32px;
  margin-inline: 8px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  font-family: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-browse:hover,
.rail-browse--selected {
  background: var(--fill-2);
  color: var(--ink);
}
.rail-browse__glyph {
  flex: 0 0 16px;
  color: var(--faint);
}
/* 数字推到右端，和侧栏里其他数字（成员数、未读）一样用等宽数字，换个数不跳位。 */
.rail-browse__count {
  margin-inline-start: auto;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
</style>
