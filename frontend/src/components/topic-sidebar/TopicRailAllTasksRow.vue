<script setup lang="ts">
// 频道下面任务那几行的最后一行：侧栏只列和我有关的几条（`lib/railTasks`），
// 频道里一共还有几条在进行，点这一行去看全部。和任务行同一种行（同字号、同一条
// 竖线、同一条文字左缘），只是字淡一档。
const props = withDefaults(
  defineProps<{
    channelId: string
    total: number
    label: string
    selected: boolean
    depth?: number
  }>(),
  { depth: 0 }
)

const emit = defineEmits<{
  (e: 'select', channelId: string): void
}>()
</script>

<template>
  <button
    type="button"
    class="rail-all"
    :class="{ 'rail-all--selected': selected }"
    :style="{ paddingInlineStart: 40 + props.depth * 20 + 'px', '--guide-x': 16 + props.depth * 20 + 'px' }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select', channelId)"
  >
    <span class="rail-all__label">{{ label }}</span>
    <span class="rail-all__count">{{ total }}</span>
  </button>
</template>

<style scoped>
.rail-all {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding-block: 0;
  padding-inline-end: 12px;
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
.rail-all:hover,
.rail-all--selected {
  background: var(--fill);
  color: var(--ink);
}
/* 竖线到这一行为止：只画上半截，收在行的中线上。 */
.rail-all::before {
  content: '';
  position: absolute;
  left: var(--guide-x);
  top: -3px;
  height: calc(50% + 3px);
  width: 1px;
  background: var(--line-2);
}
.rail-all__label {
  flex: 1 1 auto;
}
.rail-all__count {
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
</style>
