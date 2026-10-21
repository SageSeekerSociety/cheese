<script setup lang="ts">
import type { RecordNode } from '../../lib/sessionRecords'

import SiteRecordRow from './SiteRecordRow.vue'

import { t } from '@/i18n'
withDefaults(
  defineProps<{
    nodes: RecordNode[]
    raw: boolean
    loadImage: (digest: string) => Promise<string>
    memberNames?: Record<string, string>
  }>(),
  { memberNames: () => ({}) }
)
</script>

<template>
  <template v-for="node in nodes" :key="node.block.id">
    <SiteRecordRow :block="node.block" :raw="raw" :load-image="loadImage" :member-names="memberNames" />
    <div v-if="node.children.some((child) => !child.block.meta?.parent)" class="record-children">
      <SiteRecordTree
        :nodes="node.children.filter((child) => !child.block.meta?.parent)"
        :raw="raw"
        :load-image="loadImage"
        :member-names="memberNames"
      />
    </div>
    <details v-if="node.children.some((child) => child.block.meta?.parent)" class="record-children">
      <summary>{{ t('work.room.site.subagent', { count: node.children.length }) }}</summary>
      <SiteRecordTree
        :nodes="node.children.filter((child) => child.block.meta?.parent)"
        :raw="raw"
        :load-image="loadImage"
        :member-names="memberNames"
      />
    </details>
  </template>
</template>

<style scoped>
.record-children {
  margin-left: 16px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
  color: var(--muted);
}
summary {
  cursor: pointer;
  font-size: 13px;
}
</style>
