<script setup lang="ts">
// 大纲的下拉内容：按级别缩进列出标题，点了滚到那一段。
//
// 它不是正文旁边的第二道侧栏 —— 右侧那道已经被评论占了（DocCommentPanel 自己管停靠、
// 抽屉、宽度），再塞一道会跟它抢位置。这里做成一枚从顶栏打开的下拉，窄屏也放得下。
//
// 标题从编辑器节点树里取（lib/docOutline.ts），面板递进来；这里只画、只往上发「点了
// 哪一节」。没有标题时给一句空态，而不是一个打不开的空白框。
import type { OutlineHeading } from '../../../lib/docOutline'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

defineProps<{ headings: OutlineHeading[] }>()
const emit = defineEmits<{ (e: 'select', pos: number): void }>()

// 每深一级缩进一档，让层级看得出来。
function indent(level: number): string {
  return `${(level - 1) * 14}px`
}
</script>

<template>
  <div class="doc-outline">
    <div v-if="headings.length === 0" class="doc-outline__empty">
      <BaseEmptyState size="inline" :title="t('work.room.doc.outlineEmpty')" />
    </div>
    <v-list v-else density="compact" class="doc-outline__list" :aria-label="t('work.room.doc.outline')">
      <v-list-item
        v-for="(heading, index) in headings"
        :key="`${heading.pos}-${index}`"
        :title="heading.text"
        :style="{ paddingInlineStart: indent(heading.level) }"
        @click="emit('select', heading.pos)"
      />
    </v-list>
  </div>
</template>

<style scoped>
.doc-outline {
  width: min(280px, calc(100vw - 32px));
}
.doc-outline__list {
  max-height: min(420px, 60vh);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.doc-outline__empty {
  padding: 12px 16px;
}
</style>
