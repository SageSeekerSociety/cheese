<!-- 知是 2.0 项目文档树的递归节点。文档即节点：任意文档可有子文档。
     纯展示 + 事件上抛（select / add-child / rename / delete / toggle-archive），
     状态与网络请求都由 ProjectView 统一管理。 -->
<template>
  <div class="dt-node">
    <div class="dt-row" :class="{ 'dt-row--active': activeId === node.id, 'dt-row--archived': node.archived }" :style="{ paddingLeft: `${depth * 14 + 8}px` }" @click="$emit('select', node.id)">
      <v-btn
        v-if="hasChildren"
        :icon="expanded ? 'mdi-chevron-down' : 'mdi-chevron-right'"
        size="x-small"
        variant="text"
        density="compact"
        class="dt-toggle"
        @click.stop="expanded = !expanded"
      />
      <span v-else class="dt-toggle-spacer" />
      <v-icon icon="mdi-file-document-outline" size="16" class="dt-ic" />
      <span class="dt-title">{{ node.title || '无标题' }}</span>
      <v-spacer />
      <v-menu location="bottom end">
        <template #activator="{ props }">
          <v-btn icon="mdi-dots-horizontal" size="x-small" variant="text" density="compact" class="dt-more" v-bind="props" @click.stop />
        </template>
        <v-list density="compact" min-width="160">
          <v-list-item prepend-icon="mdi-plus" title="新建子文档" @click="$emit('add-child', node.id)" />
          <v-list-item prepend-icon="mdi-pencil" title="重命名" @click="$emit('rename', node)" />
          <v-list-item
            :prepend-icon="node.archived ? 'mdi-archive-arrow-up-outline' : 'mdi-archive-outline'"
            :title="node.archived ? '取消归档' : '归档'"
            @click="$emit('toggle-archive', node)"
          />
          <v-divider />
          <v-list-item prepend-icon="mdi-delete-outline" title="删除" base-color="error" @click="$emit('delete', node)" />
        </v-list>
      </v-menu>
    </div>

    <template v-if="expanded && hasChildren">
      <!-- TODO: 拖拽排序/移动（PATCH parentId+sortOrder）尚未实现。 -->
      <DocumentTreeNode
        v-for="child in visibleChildren"
        :key="child.id"
        :node="child"
        :active-id="activeId"
        :depth="depth + 1"
        :show-archived="showArchived"
        @select="$emit('select', $event)"
        @add-child="$emit('add-child', $event)"
        @rename="$emit('rename', $event)"
        @delete="$emit('delete', $event)"
        @toggle-archive="$emit('toggle-archive', $event)"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { DocumentTreeNode as TreeNode } from '@/network/api/documents'

const props = defineProps<{
  node: TreeNode
  activeId: number | null
  depth: number
  showArchived: boolean
}>()

defineEmits<{
  (e: 'select', id: number): void
  (e: 'add-child', parentId: number): void
  (e: 'rename', node: TreeNode): void
  (e: 'delete', node: TreeNode): void
  (e: 'toggle-archive', node: TreeNode): void
}>()

const expanded = ref(true)

const visibleChildren = computed(() =>
  props.showArchived ? props.node.children : props.node.children.filter((c) => !c.archived)
)
const hasChildren = computed(() => visibleChildren.value.length > 0)
</script>

<style scoped>
.dt-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px 4px 8px;
  cursor: pointer;
  border-radius: 6px;
  min-height: 30px;
}
.dt-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.dt-row--active {
  background: rgba(var(--v-theme-primary), 0.1);
}
.dt-row--archived {
  opacity: 0.5;
}
.dt-toggle,
.dt-toggle-spacer {
  width: 20px;
  height: 20px;
  flex: none;
}
.dt-ic {
  flex: none;
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
}
.dt-title {
  font-size: 13.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dt-more {
  opacity: 0;
}
.dt-row:hover .dt-more {
  opacity: 1;
}
</style>
