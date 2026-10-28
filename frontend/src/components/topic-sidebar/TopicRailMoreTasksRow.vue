<script setup lang="ts">
// 频道下面任务那几行的最后一行：侧栏先只列和我有关的几条（频道那一行的 `my_tasks`），
// 这一行就地把频道里其余进行中的任务展开在下面，再点一下收回去。它不换页：去「全部
// 任务」那一页走频道的 ⋯。和任务行同一种行（同字号、同一条竖线、同一条文字左缘），
// 只是字淡一档。
const props = withDefaults(
  defineProps<{
    channelId: string
    label: string
    expanded: boolean
    depth?: number
  }>(),
  { depth: 0 }
)

const emit = defineEmits<{
  (e: 'toggle', channelId: string): void
}>()
</script>

<template>
  <button
    type="button"
    class="rail-more"
    :style="{ '--task-indent': 32 + props.depth * 20 + 'px', '--guide-x': 16 + props.depth * 20 + 'px' }"
    :aria-expanded="expanded"
    @click="emit('toggle', channelId)"
  >
    <span class="rail-more__label">{{ label }}</span>
    <v-icon size="16" class="rail-more__chevron" aria-hidden="true">{{
      expanded ? 'mdi-chevron-up' : 'mdi-chevron-down'
    }}</v-icon>
  </button>
</template>

<style scoped>
.rail-more {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding-block: 0;
  /* 和任务行的标题同一个左缘：任务行在标题前留了 20px 给状态记号（TopicRailTaskRow.vue
     的 `--text-start`），这一行没有记号，字也从那里开始。 */
  padding-inline-start: calc(var(--task-indent) + 20px);
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
/* 静默透明 / hover --fill-2，和频道行、任务行同一套。它不是一个去处，所以没有选中态。
   --fill 压在这条 rail 的 --canvas 上只有 1.027:1，等于没画（同 TopicRailRow.vue 那段注释）。 */
.rail-more:hover {
  background: var(--fill-2);
  color: var(--ink);
}
/* 竖线到这一行为止：只画上半截，收在行的中线上。 */
.rail-more::before {
  content: '';
  position: absolute;
  left: var(--guide-x);
  top: -3px;
  height: calc(50% + 3px);
  width: 1px;
  background: var(--line-2);
}
.rail-more__label {
  flex: 1 1 auto;
}
.rail-more__chevron {
  color: var(--faint);
}
</style>
