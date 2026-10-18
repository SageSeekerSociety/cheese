<script setup lang="ts">
// 文档末尾的「被引用」（#3827）：哪些文档、哪些对话引用了这一份。只列读的人能读的，
// 一处都没有就不画。只吃 props、只往上发事件：取数在 composables/useDocLinks.ts。
import type { DocBacklink } from '../../../api/docLinks'

import { computed } from 'vue'

import { t } from '@/i18n'
import { plainRefs } from '@/lib/refChip'
import { refsVersion, rememberRef } from '@/lib/refDirectory'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  items: DocBacklink[]
  /** handle → 名字。 */
  names: Record<string, string>
}>()

const emit = defineEmits<{
  /** 打开一份文档或一个对话：去处和正文里的 `<#id>` 一样由 `refRoute` 认。 */
  (e: 'open', id: string, blockId?: string): void
}>()

const nameOf = (handle: string | null) => (handle ? props.names[handle] || handle : '')

// 文档在前，对话在后：引用它的文档多半是它的上下游，对话是讨论过它的地方。
const ordered = computed(() => [
  ...props.items.filter((i) => i.kind === 'document'),
  ...props.items.filter((i) => i.kind === 'conversation'),
])

const ICON = { room: 'mdi-pound', task: 'mdi-format-list-checks', thread: 'mdi-message-reply-text-outline' } as const

// 消息开头里的引用读成文字（@名字、#频道、文档标题）；文档标题问回来之后重画。
const excerpts = computed(() => {
  void refsVersion.value
  const names = { mentionNames: props.names, topicTitles: {}, notified: true }
  return Object.fromEntries(
    props.items.flatMap((i) => (i.kind === 'conversation' ? [[i.conversation.id, plainRefs(i.excerpt, names)]] : []))
  )
})

function open(item: DocBacklink) {
  if (item.kind === 'document') {
    rememberRef(item.id, { kind: 'document', title: item.title ?? '', room_id: '', project_id: item.project_id })
    emit('open', item.id)
    return
  }
  const where = item.conversation
  rememberRef(where.id, { kind: where.kind, title: where.title, room_id: where.room_id, project_id: where.project_id })
  emit('open', where.id, item.message_id)
}
</script>

<template>
  <section v-if="ordered.length" class="doc-backlinks" data-testid="doc-backlinks">
    <h2 class="doc-backlinks__head t-meta-read c-muted">
      {{ t('work.room.doc.backlinks.title') }}
      <span class="doc-backlinks__count">{{ ordered.length }}</span>
    </h2>
    <ul class="doc-backlinks__list">
      <li v-for="item in ordered" :key="item.kind === 'document' ? item.id : item.conversation.id">
        <button type="button" class="doc-backlinks__row" @click="open(item)">
          <v-icon size="16" class="doc-backlinks__icon">
            {{ item.kind === 'document' ? 'mdi-file-document-outline' : ICON[item.conversation.kind] }}
          </v-icon>
          <span class="doc-backlinks__main">
            <span class="doc-backlinks__title t-body c-ink">
              {{
                item.kind === 'document'
                  ? item.title || t('work.room.doc.untitled')
                  : item.conversation.kind === 'room'
                    ? `#${item.conversation.title}`
                    : item.conversation.title
              }}
            </span>
            <span v-if="item.kind === 'conversation'" class="doc-backlinks__excerpt c-text">
              {{ excerpts[item.conversation.id] }}
            </span>
          </span>
          <span class="doc-backlinks__meta c-muted">
            <template v-if="item.kind === 'document'">
              {{
                t('work.room.doc.backlinks.edited', { name: nameOf(item.updated_by), when: relTime(item.updated_at) })
              }}
            </template>
            <template v-else>
              <span>{{ nameOf(item.author) }}</span>
              <span>{{ relTime(item.at) }}</span>
              <span v-if="item.count > 1">{{ t('work.room.doc.backlinks.count', { count: item.count }) }}</span>
            </template>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.doc-backlinks {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.doc-backlinks__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  font-weight: 600;
}

.doc-backlinks__count {
  font-weight: 400;
}

.doc-backlinks__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.doc-backlinks__row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 8px;
  margin: 0 -8px;
  border-radius: var(--radius-md);
  text-align: left;
  background: transparent;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-out);
}

.doc-backlinks__row:hover,
.doc-backlinks__row:focus-visible {
  background: var(--fill);
}

.doc-backlinks__icon {
  flex: none;
  margin-top: 2px;
  color: var(--muted);
}

.doc-backlinks__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}

.doc-backlinks__excerpt,
.doc-backlinks__meta {
  font-size: 13px;
  line-height: var(--lh-13);
}

.doc-backlinks__title,
.doc-backlinks__excerpt {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.doc-backlinks__meta {
  display: flex;
  flex: none;
  gap: 8px;
  margin-top: 2px;
}
</style>
