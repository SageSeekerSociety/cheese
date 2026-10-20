<template>
  <!-- The way into 芝士 on a page header: its face and its name, as on the document's top bar
       (DocTopBar). Narrow headers keep the face alone; the name stays as the button's label. -->
  <button
    type="button"
    class="agent-entry"
    :class="{ 'agent-entry--compact': compact }"
    :aria-label="name"
    :title="name"
    v-bind="$attrs"
  >
    <CheeseAvatar :size="18" :name="name" :handle="handle" />
    <span v-if="!compact" class="agent-entry__label">{{ name }}</span>
  </button>
</template>

<script setup lang="ts">
// 页头上打开芝士的那一颗：头像加名字，和文档顶栏上的同一个样子。只画，点了由外面处理。
import CheeseAvatar from '@/components/CheeseAvatar.vue'

defineOptions({ inheritAttrs: false })

withDefaults(
  defineProps<{
    name: string
    handle?: string | null
    /** 窄的页头只放头像。 */
    compact?: boolean
  }>(),
  { handle: null, compact: false }
)
</script>

<style scoped>
.agent-entry {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  height: 36px;
  min-width: 36px;
  padding: 0 10px;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  flex: 0 0 auto;
}

.agent-entry--compact {
  justify-content: center;
  padding: 0;
}

.agent-entry:hover {
  background: var(--fill);
}

.agent-entry:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

@media (pointer: coarse) {
  .agent-entry {
    min-width: 44px;
    height: 44px;
  }
}
</style>
