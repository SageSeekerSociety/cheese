<script setup lang="ts">
// 频道概览里「支线」那一格：这个频道里有人回过话的支线，最近有回复的在前。每一行写
// 它挂着的那条消息、最后一句回复（最多两行）、几条回复和谁说过话；转成了任务的，
// 写那个任务和它的状态。只画，取数在拿着这个频道的那一层。
import type { Block } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'
import type { ThreadReply, ThreadRow } from '../../types/threads'

import { replySnippet } from '../../lib/blockDisplay'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'

import { t } from '@/i18n'

const props = defineProps<{
  rows: ThreadRow[]
  loading: boolean
  error: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  fmtTime: (iso: string) => string
}>()

const emit = defineEmits<{
  (e: 'open', threadId: string): void
  (e: 'open-task', taskId: string): void
}>()

function said(reply: ThreadReply | null, max: number): string {
  if (!reply) return ''
  const block = {
    id: '',
    conversation_id: '',
    kind: 'message',
    author_type: 'participant',
    author: reply.author,
    content: reply.content,
    created_at: reply.created_at,
  } as Block
  return t('work.room.thread.lastReply', {
    name: props.nameOf(reply.author),
    text: replySnippet(block, props.refs, max),
  })
}

function people(row: ThreadRow): string {
  return t('work.room.thread.people', {
    replies: t('work.room.thread.replies', { count: row.reply_count }),
    names: row.participants.map(props.nameOf).join(t('work.room.roster.listSeparator')),
  })
}
</script>

<template>
  <div class="threads">
    <LoadingSkeleton v-if="loading && !rows.length" variant="list" />
    <p v-else-if="error && !rows.length" class="threads__note t-meta" role="alert">{{ error }}</p>
    <p v-else-if="!rows.length" class="threads__note t-meta">{{ t('work.room.thread.empty') }}</p>
    <template v-else>
      <p class="threads__caption t-meta">{{ t('work.room.thread.listCaption') }}</p>
      <ul class="threads__list">
        <li v-for="row in rows" :key="row.id">
          <button type="button" class="thread-row" data-testid="thread-row" @click="emit('open', row.id)">
            <span class="thread-row__head">
              <span
                class="thread-row__dot"
                :class="{ 'thread-row__dot--on': row.unread }"
                :aria-label="row.unread ? t('work.room.thread.unread') : undefined"
              />
              <span class="thread-row__root">{{ said(row.root, 80) }}</span>
              <span v-if="row.last_reply_at" class="thread-row__time t-meta">{{ fmtTime(row.last_reply_at) }}</span>
            </span>
            <span v-if="row.task" class="thread-row__task">
              <span
                class="thread-row__task-link"
                role="link"
                tabindex="0"
                @click.stop="emit('open-task', row.task.id)"
                @keydown.enter.stop="emit('open-task', row.task.id)"
                >{{ t('work.room.thread.becameTask', { title: row.task.title }) }}</span
              >
              <span class="thread-row__chip">{{
                row.task.status === 'open'
                  ? t('work.room.thread.taskStatus.open')
                  : t('work.room.thread.taskStatus.closed')
              }}</span>
            </span>
            <span v-else-if="row.last_reply" class="thread-row__last">{{ said(row.last_reply, 200) }}</span>
            <span class="thread-row__people t-meta">{{ people(row) }}</span>
          </button>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.threads {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow-y: auto;
}
.threads__note,
.threads__caption {
  margin: 0;
  padding: 12px 16px 4px;
  color: var(--muted);
}
.threads__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.thread-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 12px 16px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.thread-row:hover {
  background: var(--fill);
}
.thread-row__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.thread-row__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}
.thread-row__dot--on {
  background: var(--accent);
}
.thread-row__root {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
}
.thread-row__time {
  flex: none;
  color: var(--faint);
}
.thread-row__last,
.thread-row__task,
.thread-row__people {
  padding-left: 16px;
}
.thread-row__last {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.thread-row__task {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  line-height: var(--lh-13);
}
.thread-row__task-link {
  color: var(--ink);
  font-weight: 600;
}
.thread-row__task-link:hover {
  text-decoration: underline;
}
.thread-row__chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.thread-row__people {
  color: var(--faint);
}
</style>
