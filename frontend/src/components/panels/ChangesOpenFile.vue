<script setup lang="ts">
// 「打开其他文件」：在这个任务的版本里按名字找一个文件打开。
//
// 树上只列这次改到的文件，审阅看的就是它们；顺手看一眼旁边那个没动过的文件，是从这里
// 搜，不是把整棵仓库树摊进来。
import type { WorkspaceFile } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '../common/AdaptiveDialog.vue'

import { t } from '@/i18n'

const props = defineProps<{ files: WorkspaceFile[] }>()

const open = defineModel<boolean>({ default: false })

const emit = defineEmits<{ (e: 'pick', path: string): void }>()

const query = ref('')
watch(open, (on) => {
  if (on) query.value = ''
})

// 列出来的最多这么多：再多就该多打几个字了。
const LIMIT = 50

// 按空格切成几段，每段都要出现在路径里（不分大小写）；文件名里就有的排在前面。
const matches = computed(() => {
  const words = query.value.toLowerCase().split(/\s+/).filter(Boolean)
  const scored: { path: string; score: number }[] = []
  for (const f of props.files) {
    const path = f.path.toLowerCase()
    if (!words.every((w) => path.includes(w))) continue
    const name = path.slice(path.lastIndexOf('/') + 1)
    scored.push({ path: f.path, score: words.filter((w) => name.includes(w)).length })
  }
  scored.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path))
  return scored.slice(0, LIMIT).map((s) => s.path)
})

function pick(path: string) {
  emit('pick', path)
}
function pickFirst() {
  const first = matches.value[0]
  if (first) pick(first)
}
</script>

<template>
  <AdaptiveDialog v-model="open" :title="t('work.room.changes.openOther')" size="md">
    <v-text-field
      v-model="query"
      autofocus
      autocomplete="off"
      variant="outlined"
      density="compact"
      hide-details
      prepend-inner-icon="mdi-magnify"
      :placeholder="t('work.room.changes.searchFiles')"
      :aria-label="t('work.room.changes.searchFiles')"
      class="mb-2"
      @keydown.enter.prevent="pickFirst"
    />
    <v-list v-if="matches.length" density="compact" class="py-0 open-file__list">
      <v-list-item v-for="path in matches" :key="path" @click="pick(path)">
        <v-list-item-title class="open-file__path">{{ path }}</v-list-item-title>
      </v-list-item>
    </v-list>
    <p v-else class="open-file__empty">{{ t('work.room.changes.noFiles') }}</p>
  </AdaptiveDialog>
</template>

<style scoped>
.open-file__list {
  max-height: 360px;
  overflow-y: auto;
}
.open-file__path {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.open-file__empty {
  margin: 0;
  padding: 12px 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
