<script setup lang="ts">
// 后台与反馈页用的空态。长相统一到 `components/base/BaseEmptyState.vue`，这里只是
// 留给已有调用处的别名：`compact` 映射成 `size="compact"`，其余属性与插槽原样转交。
// 新代码直接用 BaseEmptyState。
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

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
  { desc: undefined, icon: 'mdi-tray-remove', action: undefined, tone: 'neutral', compact: false }
)

const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <BaseEmptyState
    :title="title"
    :desc="desc"
    :icon="icon"
    :action="action"
    :tone="tone"
    :size="compact ? 'compact' : 'page'"
    @action="emit('action')"
  >
    <slot />
  </BaseEmptyState>
</template>
