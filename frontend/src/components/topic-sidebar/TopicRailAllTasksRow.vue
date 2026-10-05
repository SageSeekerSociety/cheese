<script setup lang="ts">
// 频道下面任务那几行的最后一行：侧栏只列和我有关的几条（`lib/railTasks`），
// 频道里一共还有几条在进行，点这一行去看全部。
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
    :style="{ paddingInlineStart: 30 + props.depth * 20 + 'px' }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select', channelId)"
  >
    <svg class="rail-all__icon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M2 3.5h10M2 7h10M2 10.5h6" />
    </svg>
    <span class="rail-all__label">{{ label }}</span>
    <span class="rail-all__count">{{ total }}</span>
  </button>
</template>

<style scoped>
.rail-all {
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
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-all:hover,
.rail-all--selected {
  background: var(--fill);
  color: var(--ink);
}
.rail-all__icon {
  flex: none;
  fill: none;
  stroke: var(--faint);
  stroke-width: 1.3;
}
.rail-all__label {
  flex: 1 1 auto;
}
.rail-all__count {
  color: var(--faint);
}
</style>
