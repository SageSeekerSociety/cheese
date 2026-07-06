<!-- Zone 3: the right context pane. Shows whatever business object the user is
     looking at (a document / a work item), driven by clicks on references in
     chat or items in the nav list. Collapsible; empty state prompts the idea. -->
<template>
  <div class="wa-ctx">
    <div class="wa-ctx__bar">
      <span class="text-caption text-medium-emphasis">状态 · 当前对象</span>
      <v-spacer />
      <v-btn size="x-small" variant="text" icon="mdi-close" @click="ws.closeContext" />
    </div>
    <div class="wa-ctx__content">
      <WorkItemPanel v-if="ws.state.context?.type === 'workitem'" :key="ws.state.context.id" :work-item-id="ws.state.context.id" />
      <DocumentPanel v-else-if="ws.state.context?.type === 'document'" :key="ws.state.context.id" :document-id="ws.state.context.id" />
      <div v-else class="wa-ctx__empty">
        <v-icon icon="mdi-cursor-default-click-outline" size="40" class="mb-3" />
        <div>点击消息里的引用，或左侧的文档 / 事项</div>
        <div class="text-caption">对话是过程，点引用看状态</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useWorkspace } from '../useWorkspace'

import DocumentPanel from './DocumentPanel.vue'
import WorkItemPanel from './WorkItemPanel.vue'

const ws = useWorkspace()
</script>

<style scoped>
.wa-ctx {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-left: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-ctx__bar {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-ctx__content {
  flex: 1;
  min-height: 0;
}
.wa-ctx__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(var(--v-theme-on-surface), 0.5);
  text-align: center;
  gap: 2px;
}
</style>
