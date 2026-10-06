<script setup lang="ts">
// 资料库搜索时文档那两组：资料库里的文档（按标题和正文），对话里的文档（按正文）。
// 对话里的那一份不进资料库列表，在这里找得到：点开回那个对话，或者另存一份进来。
import type { DocumentHit } from '../../lib/projectDocument'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'

defineProps<{
  library: DocumentHit[]
  rooms: DocumentHit[]
  /** 正在另存的那一份（按钮转圈）。 */
  keeping?: string | null
}>()

const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'open-room', topicId: string): void
  (e: 'keep', id: string): void
}>()

function roomName(hit: DocumentHit): string {
  return topicTitle({ title: hit.room_title ?? '' })
}
</script>

<template>
  <section v-if="library.length" :aria-label="t('work.library.hits.library')" class="doc-hits">
    <h2 class="t-meta c-faint doc-hits__head">{{ t('work.library.hits.libraryCount', { n: library.length }) }}</h2>
    <button v-for="hit in library" :key="hit.id" type="button" class="doc-hit" @click="emit('open', hit.id)">
      <v-icon icon="mdi-file-document-edit-outline" size="20" class="doc-hit__icon" />
      <span class="doc-hit__id">
        <span class="t-body doc-hit__name">{{ hit.title || t('work.room.doc.untitled') }}</span>
        <span v-if="hit.snippet" class="t-meta c-muted doc-hit__snippet">{{ hit.snippet }}</span>
      </span>
    </button>
  </section>
  <section v-if="rooms.length" :aria-label="t('work.library.hits.rooms')" class="doc-hits">
    <h2 class="t-meta c-faint doc-hits__head">{{ t('work.library.hits.roomsCount', { n: rooms.length }) }}</h2>
    <div v-for="hit in rooms" :key="hit.id" class="doc-hit doc-hit--room">
      <button type="button" class="doc-hit__open" @click="hit.topic_id && emit('open-room', hit.topic_id)">
        <v-icon icon="mdi-message-text-outline" size="20" class="doc-hit__icon" />
        <span class="doc-hit__id">
          <span class="t-body doc-hit__name">{{ t('work.library.hits.roomDoc', { room: roomName(hit) }) }}</span>
          <span v-if="hit.snippet" class="t-meta c-muted doc-hit__snippet">{{ hit.snippet }}</span>
        </span>
      </button>
      <BaseButton kind="secondary" size="sm" :loading="keeping === hit.id" @click="emit('keep', hit.id)">
        {{ t('work.library.hits.keep') }}
      </BaseButton>
    </div>
  </section>
</template>

<style scoped>
.doc-hits {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 12px;
}
.doc-hits__head {
  margin: 0;
  padding: 4px 8px;
  font-weight: 600;
}
.doc-hit {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.doc-hit--room {
  cursor: default;
}
.doc-hit:hover,
.doc-hit__open:hover {
  background: var(--fill);
}
.doc-hit__open {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 0;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.doc-hit__icon {
  flex: none;
  color: var(--muted);
}
.doc-hit__id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.doc-hit__name,
.doc-hit__snippet {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
