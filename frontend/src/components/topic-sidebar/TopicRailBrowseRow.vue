<script setup lang="ts">
// 频道列表的最后一行：「浏览频道」。侧栏只列我加入了的频道，别的频道从这里找——
// 去项目设置的「频道」一栏，看全部频道、加入或退出。和频道行同一条左缘（8px 起 +
// 16px 的槽），字淡一档：它是个入口，不是一个频道。
defineProps<{ label: string; selected: boolean }>()
const emit = defineEmits<{ (e: 'select'): void }>()
</script>

<template>
  <button
    type="button"
    class="rail-browse"
    :class="{ 'rail-browse--selected': selected }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select')"
  >
    <v-icon size="16" class="rail-browse__glyph" icon="mdi-magnify" />
    <span>{{ label }}</span>
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
</style>
