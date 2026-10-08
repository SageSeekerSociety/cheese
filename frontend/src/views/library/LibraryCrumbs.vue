<script setup lang="ts">
// 资料库里现在在哪一层：从最上层到这一层的每一级，点哪一级回到哪一级。最后一级就是
// 这一层，不可点。
import { computed } from 'vue'

import { t } from '@/i18n'
import { crumbs } from '@/lib/libraryFolders'

const props = defineProps<{
  /** 这一层的整条路径，`合同/2026`。 */
  dir: string
  /** 最上层叫什么（「资料库」）。 */
  root: string
}>()

const emit = defineEmits<{ go: [dir: string] }>()

const levels = computed(() => crumbs(props.dir))
</script>

<template>
  <nav class="library-crumbs t-body" :aria-label="t('work.library.crumbsLabel')">
    <button type="button" class="library-crumbs__step" @click="emit('go', '')">{{ props.root }}</button>
    <template v-for="(level, i) in levels" :key="level.path">
      <v-icon icon="mdi-chevron-right" size="16" class="c-faint" />
      <span v-if="i === levels.length - 1" class="library-crumbs__here" aria-current="page">{{ level.name }}</span>
      <button v-else type="button" class="library-crumbs__step" @click="emit('go', level.path)">
        {{ level.name }}
      </button>
    </template>
  </nav>
</template>

<style scoped>
.library-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.library-crumbs__step {
  padding: 2px 6px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.library-crumbs__step:hover {
  background: var(--fill);
}

.library-crumbs__here {
  padding: 2px 6px;
  color: var(--ink);
  overflow-wrap: anywhere;
}
</style>
