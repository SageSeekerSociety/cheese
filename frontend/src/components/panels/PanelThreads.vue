<script setup lang="ts">
// 频道概览里「支线」那一格：这个频道里有人回过话的支线，最近有回复的在前。每一行写
// 它挂着的那条消息、最后一句回复（最多两行）、几条回复和谁说过话；转成了任务的，
// 写那个任务和它的状态。只画，取数在拿着这个频道的那一层。
import type { RefNames } from '../../lib/refChip'
import type { ThreadReply, ThreadRow } from '../../types/threads'

import { ref } from 'vue'

import { replyBlock, replySnippet } from '../../lib/blockDisplay'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

const props = defineProps<{
  rows: ThreadRow[]
  /** 收起来的那些（`thread/recycle.py`）：报错满三天、没人回来的支线，点开还能读。 */
  recycled: ThreadRow[]
  loading: boolean
  error: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  fmtTime: (iso: string) => string
  /** 我能不能在这个频道里开支线（外部协作者不能，见后端 `threadOpenedByPerson`）。 */
  canCreate: boolean
  /** 我能不能把收起来的放回来（开这条支线的人，或频道管理员；后端同一条判据）。 */
  canRestore?: (row: ThreadRow) => boolean
}>()

const emit = defineEmits<{
  (e: 'open', threadId: string): void
  (e: 'open-task', taskId: string): void
  // 两处「新建支线」点了都走这里（顶上常驻那一颗、空态里那一颗，清单第 4、12 条）：
  // 这一格只画，开对话框、发请求由拿着这个频道的父级做。
  (e: 'new'): void
  /** 把一条收起来的支线放回来；请求由父级发（它拿着这个频道）。 */
  (e: 'restore', threadId: string): void
}>()

/** 「已回收」那一栏开着没有：平时收着，不占地方。 */
const showingRecycled = ref(false)

function said(reply: ThreadReply | null, max: number): string {
  if (!reply) return ''
  return t('work.room.thread.lastReply', {
    name: props.nameOf(reply.author),
    text: replySnippet(replyBlock(reply), props.refs, max),
  })
}

function people(row: ThreadRow): string {
  return t('work.room.thread.people', {
    replies: t('work.room.thread.replies', { count: row.reply_count }),
    names: row.participants.map(props.nameOf).join(t('work.room.roster.listSeparator')),
  })
}

/** 这一条我放不放得回来：开这条支线的人，或者频道管理员（后端 `threadRestoreNotYours`
 *  同一条）。看的人是谁这里不知道，父级会给一个判据；不给就是能。 */
function mayRestore(row: ThreadRow): boolean {
  return props.canRestore ? props.canRestore(row) : true
}
</script>

<template>
  <div class="threads">
    <!-- 顶上常驻那一颗「新建支线」（清单第 4 条）：支线页一进来就看得见它，不必先在
         主线上找一条消息去「在支线中回复」。频道冻结了就没有这条——后端也会拒。 -->
    <div v-if="canCreate" class="threads__head">
      <BaseButton kind="secondary" size="sm" data-testid="new-thread" @click="emit('new')">
        {{ t('work.room.thread.new') }}
      </BaseButton>
    </div>
    <LoadingSkeleton v-if="loading && !rows.length" variant="list" />
    <p v-else-if="error && !rows.length" class="threads__note t-meta" role="alert">{{ error }}</p>
    <BaseEmptyState
      v-else-if="!rows.length"
      size="compact"
      class="threads__note"
      :title="t('work.room.thread.empty')"
      :desc="t('work.room.thread.emptyHint')"
      :action="canCreate ? t('work.room.thread.new') : undefined"
      @action="emit('new')"
    />
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
            <span v-for="task in row.tasks" :key="task.id" class="thread-row__task">
              <span
                class="thread-row__task-link"
                role="link"
                tabindex="0"
                @click.stop="emit('open-task', task.id)"
                @keydown.enter.stop="emit('open-task', task.id)"
                >{{ t('work.room.thread.taskLink', { title: task.title }) }}</span
              >
              <span class="thread-row__chip">{{
                task.status === 'open' ? t('work.room.thread.taskStatus.open') : t('work.room.thread.taskStatus.closed')
              }}</span>
            </span>
            <span v-if="!row.tasks.length && row.last_reply" class="thread-row__last">{{
              said(row.last_reply, 200)
            }}</span>
            <span class="thread-row__people t-meta">{{ people(row) }}</span>
          </button>
        </li>
      </ul>
    </template>
    <!-- 已回收（`thread/recycle.py`）：报错满三天、没人回来的支线。平时收着，要看才点开；
         每一行读得到、也点得进去，能恢复的旁边有一颗「恢复」。 -->
    <template v-if="recycled.length">
      <button
        type="button"
        class="threads__recycled-head t-meta"
        :aria-expanded="showingRecycled"
        data-testid="recycled-toggle"
        @click="showingRecycled = !showingRecycled"
      >
        {{ t('work.room.thread.recycled') }} · {{ recycled.length }}
      </button>
      <template v-if="showingRecycled">
        <p class="threads__caption t-meta">{{ t('work.room.thread.recycledCaption') }}</p>
        <ul class="threads__list">
          <li v-for="row in recycled" :key="row.id">
            <div class="thread-row thread-row--recycled">
              <button type="button" class="thread-row__body" data-testid="recycled-row" @click="emit('open', row.id)">
                <span class="thread-row__head">
                  <span class="thread-row__root">{{ said(row.root, 80) }}</span>
                  <span v-if="row.recycled_at" class="thread-row__time t-meta">{{
                    t('work.room.thread.recycledAt', { time: fmtTime(row.recycled_at) })
                  }}</span>
                </span>
                <span v-if="row.last_reply" class="thread-row__last">{{ said(row.last_reply, 200) }}</span>
                <span class="thread-row__people t-meta">{{ people(row) }}</span>
              </button>
              <BaseButton
                v-if="mayRestore(row)"
                kind="ghost"
                size="sm"
                data-testid="restore-thread"
                @click="emit('restore', row.id)"
              >
                {{ t('work.room.thread.restore') }}
              </BaseButton>
            </div>
          </li>
        </ul>
      </template>
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
.threads__head {
  display: flex;
  padding: 8px 16px 0;
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
/* 已回收那一栏的头：一行 13px 的字，点开点合，画得比行还安静。 */
.threads__recycled-head {
  width: 100%;
  margin-top: 8px;
  padding: 8px 16px;
  border: 0;
  border-top: 1px solid var(--line);
  background: none;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
}
.threads__recycled-head:hover {
  color: var(--text);
}
/* 已回收的一行：同一个形状，头里那颗「恢复」在右边而不是压着整行。 */
.thread-row--recycled {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  padding: 0 8px 0 0;
}
.thread-row__body {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  border: 0;
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
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
