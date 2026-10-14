<script setup lang="ts">
// 频道概览里「支线」那一格：这个频道里有人回过话的支线，最近有回复的在前。每一行写
// 它挂着的那条消息、最后一句回复（最多两行）、几条回复和谁说过话；转成了任务的，
// 写那个任务和它的状态。只画，取数在拿着这个频道的那一层。
import type { MemberFace, MemberFaces } from '../../lib/memberFaces'
import type { RefNames } from '../../lib/refChip'
import type { ThreadReply, ThreadRow } from '../../types/threads'

import { replyBlock, replySnippet } from '../../lib/blockDisplay'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRef.vue'
import { t } from '@/i18n'

const props = defineProps<{
  rows: ThreadRow[]
  loading: boolean
  error: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  fmtTime: (iso: string) => string
  /** 名册来的身份（头像 id、队友身份）。有它就不用拿名字反查；没有时退回 handle + 彩色首字母。 */
  faces?: MemberFaces
  /** 我能不能在这个频道里开支线（外部协作者不能，见后端 `threadOpenedByPerson`）。 */
  canCreate: boolean
}>()

const emit = defineEmits<{
  (e: 'open', threadId: string): void
  (e: 'open-task', taskId: string): void
  // 两处「新建支线」点了都走这里（顶上常驻那一颗、空态里那一颗，清单第 4、12 条）：
  // 这一格只画，开对话框、发请求由拿着这个频道的父级做。
  (e: 'new'): void
}>()

/** 要画的那个人：名册有这一行就用它，否则 null（视图退回 handle + 彩色首字母）。 */
function faceOf(handle: string): MemberFace | null {
  return props.faces?.[handle] ?? null
}
function faceName(handle: string): string {
  return faceOf(handle)?.name ?? props.nameOf(handle)
}
/** 谁说的那句里的正文（@ 过的人、提过的话题读成名字，和对话里一样）。人名走 `#name` 槽。 */
function replyText(reply: ThreadReply | null, max: number): string {
  if (!reply) return ''
  return replySnippet(replyBlock(reply), props.refs, max)
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
              <span v-if="row.root" class="thread-row__root">
                <i18n-t keypath="work.room.thread.lastReply" scope="global" tag="span">
                  <template #name>
                    <UserRef
                      :handle="row.root.author"
                      :name="faceName(row.root.author)"
                      :avatar-id="faceOf(row.root.author)?.avatarId"
                      :agent="faceOf(row.root.author)?.agent"
                    />
                  </template>
                  <template #text>{{ replyText(row.root, 80) }}</template>
                </i18n-t>
              </span>
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
            <span v-if="!row.tasks.length && row.last_reply" class="thread-row__last">
              <i18n-t keypath="work.room.thread.lastReply" scope="global" tag="span">
                <template #name>
                  <UserRef
                    :handle="row.last_reply.author"
                    :name="faceName(row.last_reply.author)"
                    :avatar-id="faceOf(row.last_reply.author)?.avatarId"
                    :agent="faceOf(row.last_reply.author)?.agent"
                  />
                </template>
                <template #text>{{ replyText(row.last_reply, 200) }}</template>
              </i18n-t>
            </span>
            <span class="thread-row__people t-meta">
              <i18n-t keypath="work.room.thread.people" scope="global" tag="span">
                <template #replies>{{ t('work.room.thread.replies', { count: row.reply_count }) }}</template>
                <template #names>
                  <template v-for="(handle, index) in row.participants" :key="handle"
                    ><span v-if="index">{{ t('work.room.roster.listSeparator') }}</span
                    ><UserRef
                      :handle="handle"
                      :name="faceName(handle)"
                      :avatar-id="faceOf(handle)?.avatarId"
                      :agent="faceOf(handle)?.agent"
                  /></template>
                </template>
              </i18n-t>
            </span>
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
