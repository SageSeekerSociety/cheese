<!-- Context pane: a document. Nodes are editable *state* — click a paragraph to
     edit it in place (editing a document is how you give the AI an instruction).
     Append-only chat and editable docs share one block substrate; the difference
     is only that a doc node mutates rather than appends. -->
<template>
  <div v-if="detail" class="wa-doc">
    <div class="wa-doc__head">
      <v-icon icon="mdi-file-document-outline" color="indigo" class="mr-2" />
      <span class="wa-doc__title">{{ detail.document.title }}</span>
    </div>
    <div class="wa-doc__body">
      <div v-for="n in detail.nodes" :key="n.id" class="wa-node" :class="`wa-node--${n.kind}`">
        <template v-if="editingId === n.id">
          <v-textarea
            v-model="draft"
            auto-grow
            rows="1"
            hide-details
            density="compact"
            variant="outlined"
            autofocus
            @blur="save(n.id)"
            @keydown.enter.exact.prevent="save(n.id)"
          />
        </template>
        <template v-else>
          <div v-if="n.kind === 'heading'" class="wa-node__heading" @click="edit(n.id, n.content)">{{ n.content }}</div>
          <div v-else-if="n.kind === 'todo'" class="wa-node__todo" @click="edit(n.id, n.content)">
            <v-icon icon="mdi-checkbox-blank-outline" size="18" class="mr-1" />{{ n.content }}
          </div>
          <div v-else-if="n.kind === 'bullet'" class="wa-node__bullet" @click="edit(n.id, n.content)">• {{ n.content }}</div>
          <pre v-else-if="n.kind === 'code'" class="wa-node__code" @click="edit(n.id, n.content)">{{ n.content }}</pre>
          <div v-else class="wa-node__p" @click="edit(n.id, n.content)">{{ n.content }}</div>
        </template>
      </div>
      <div v-if="!detail.nodes.length" class="text-medium-emphasis">空文档</div>
    </div>
  </div>
  <div v-else class="wa-doc__loading"><v-progress-circular indeterminate color="primary" /></div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import type { DocumentDetail } from '@/network/api/workspace'
import { WorkspaceApi } from '@/network/api/workspace'

import { useWorkspace } from '../useWorkspace'

const props = defineProps<{ documentId: number }>()
const ws = useWorkspace()

const detail = ref<DocumentDetail | null>(null)
const editingId = ref<number | null>(null)
const draft = ref('')

async function load() {
  detail.value = null
  const res = await WorkspaceApi.getDocument(props.documentId)
  detail.value = res.data
}

function edit(id: number, content: string) {
  editingId.value = id
  draft.value = content
}

async function save(nodeId: number) {
  if (editingId.value !== nodeId) return
  const node = detail.value?.nodes.find((n) => n.id === nodeId)
  editingId.value = null
  if (!node || node.content === draft.value) return
  await WorkspaceApi.editNode(props.documentId, nodeId, { content: draft.value })
  await load()
  ws.refreshDocuments()
}

watch(() => props.documentId, load, { immediate: true })
</script>

<style scoped>
.wa-doc {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.wa-doc__head {
  display: flex;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-doc__title {
  font-size: 16px;
  font-weight: 700;
}
.wa-doc__body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}
.wa-node {
  margin-bottom: 6px;
}
.wa-node__heading {
  font-size: 20px;
  font-weight: 700;
  cursor: text;
  padding: 4px 0;
}
.wa-node__p,
.wa-node__todo,
.wa-node__bullet {
  font-size: 15px;
  line-height: 1.7;
  cursor: text;
  padding: 2px 4px;
  border-radius: 4px;
}
.wa-node__p:hover,
.wa-node__todo:hover,
.wa-node__bullet:hover,
.wa-node__heading:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.wa-node__code {
  font-family: monospace;
  font-size: 13px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  padding: 10px 12px;
  border-radius: 8px;
  cursor: text;
  white-space: pre-wrap;
}
.wa-doc__loading {
  display: flex;
  justify-content: center;
  padding: 40px;
}
</style>
