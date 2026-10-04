<script setup lang="ts">
/**
 * BaseTable 的表头格子。给了 `sortKey` 就是可排序的一列：整格是一颗按钮，点了让表
 * 发 `sort`；当前排序的那一列带方向箭头和 `aria-sort`，读屏念得出「按这一列升序」。
 * 不给 `sortKey` 就是一个普通的 `<th scope="col">`。
 */
import { computed, inject } from 'vue'

import { TABLE_SORT } from './tableSort'

const props = withDefaults(defineProps<{ sortKey?: string; align?: 'start' | 'center' | 'end' }>(), {
  sortKey: undefined,
  align: 'start',
})

const sort = inject(TABLE_SORT, null)
const active = computed(() => !!props.sortKey && sort?.key.value === props.sortKey)
const ariaSort = computed(() => {
  if (!props.sortKey) return undefined
  if (!active.value) return 'none'
  return sort?.dir.value === 'asc' ? 'ascending' : 'descending'
})
</script>

<template>
  <th scope="col" class="btth" :class="`btth--${align}`" :aria-sort="ariaSort">
    <button v-if="sortKey && sort" type="button" class="btth__sort" @click="sort.toggle(sortKey)">
      <slot />
      <v-icon
        size="14"
        class="btth__arrow"
        :class="{ 'btth__arrow--on': active }"
        :icon="active && sort.dir.value === 'desc' ? 'mdi-arrow-down' : 'mdi-arrow-up'"
        aria-hidden="true"
      />
    </button>
    <slot v-else />
  </th>
</template>

<style scoped>
/* Three selectors deep so it beats BaseTable's `.agrid__head :deep(th)` left alignment. */
th.btth.btth--center {
  text-align: center;
}

th.btth.btth--end {
  text-align: end;
}

.btth__sort {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 0;
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.btth__sort:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

/* The arrow only shows on the sorted column, and as a hover hint on the others. */
.btth__arrow {
  color: var(--faint);
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}

.btth__sort:hover .btth__arrow,
.btth__arrow--on {
  opacity: 1;
}

.btth__arrow--on {
  color: var(--text);
}
</style>
