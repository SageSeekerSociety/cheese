<script setup lang="ts">
// 攒着的那几处批注，逐条列在输入框上方：哪一份文件的哪一处、引的哪句原文、要芝士做什么、
// 去处是哪。攒够了一次发，芝士一轮改完，不用一条一条打断、每来一条就重排一版。
//
// 和「芝士会看到你在看什么」一样是注入进来的（`lib/pendingNotes`）：话题页提供，右栏预览
// 往里加，这里读它、删它、发它。没有这个（私聊、别处的输入框）时整块不出现。
import type { PendingNote } from '@/lib/pendingNotes'

import { computed, inject, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { PENDING_NOTES, pendingWhere, sendableCount } from '@/lib/pendingNotes'

const pending = inject(PENDING_NOTES, null)

/** 折叠起来还是摊开：默认摊开（刚加进来就看得见自己加的是什么），点标题收起来。 */
const open = ref(true)

const notes = computed<readonly PendingNote[]>(() => pending?.notes.value ?? [])
const count = computed(() => notes.value.length)
/** 只留评论的那几条不进「全部发送」那一条消息：评论不经对话发。 */
const sendable = computed(() => sendableCount(notes.value))

function audienceLabel(note: PendingNote): string {
  return note.audience === 'comment' ? t('work.room.preview.recipientComment') : t('work.room.preview.pendingToCheese')
}

/** 上一次发送没成的那一句：对话栏那一刻还没准备好（`say` 收不下）时，这一批留着，
 *  得让人看得出「按了没反应」是没发出去，不是发出去没显示。下一回重试成功就抹掉。 */
// 发送失败记在篮上（三个入口共用一条），这里只读它：下一次成功、或者这一批被清空时
// 它自己会清掉。
const sendError = computed(() => (pending?.failed.value ? t('work.room.preview.sendQueuedFailed') : ''))

function sendAll() {
  if (!sendable.value) return
  pending?.send('')
}
</script>

<template>
  <div v-if="pending && count" class="pending">
    <!-- 标题那一行：左半边收放，右边那颗「全部发送」是并列的另一个按钮——
         按钮里不能再套一个按钮。 -->
    <div class="pending__head">
      <button
        type="button"
        class="pending__toggle"
        :aria-expanded="open"
        data-testid="pending-notes-head"
        @click="open = !open"
      >
        <span class="pending__badge" aria-hidden="true">{{ count }}</span>
        <b class="pending__title">{{ t('work.room.preview.pendingCount', { count }) }}</b>
        <span class="pending__why t-meta">{{ t('work.room.preview.pendingWhy') }}</span>
        <v-icon size="16">{{ open ? 'mdi-chevron-down' : 'mdi-chevron-up' }}</v-icon>
      </button>
      <BaseButton kind="primary" size="sm" :disabled="!sendable" data-testid="pending-notes-send-all" @click="sendAll">
        {{ t('work.room.preview.sendQueued', { count: sendable }) }}
      </BaseButton>
    </div>
    <ul v-if="open" class="pending__list" data-testid="pending-notes-list">
      <li v-for="(note, i) in notes" :key="note.id" class="pending__row" data-testid="pending-note">
        <span class="pending__pin" aria-hidden="true">{{ i + 1 }}</span>
        <div class="pending__main">
          <div class="pending__where t-meta" :title="pendingWhere(note)">
            <v-icon size="13" class="pending__where-icon">mdi-crosshairs-gps</v-icon>
            <span class="pending__where-text">{{ pendingWhere(note) }}</span>
          </div>
          <div v-if="note.quote" class="pending__quote" :title="note.quote">「{{ note.quote }}」</div>
          <div class="pending__text">{{ note.text }}</div>
        </div>
        <div class="pending__side">
          <span class="pending__chip">{{ audienceLabel(note) }}</span>
          <button
            type="button"
            class="pending__x"
            :title="t('work.room.preview.pendingRemove')"
            :aria-label="t('work.room.preview.pendingRemove')"
            data-testid="pending-note-remove"
            @click="pending?.remove(note.id)"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
      </li>
    </ul>
    <p v-else class="pending__hint t-meta">{{ t('work.room.preview.pendingCollapsedHint') }}</p>
    <p v-if="sendError" class="pending__error" role="alert">{{ sendError }}</p>
  </div>
</template>

<style scoped>
/* 一整块像原型里那样：琥珀色描边的托盘，贴在输入框上面那一格。 */
.pending {
  border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line-2));
  background: var(--accent-wash);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.pending__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}
.pending__toggle {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}
.pending__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: var(--accent-on);
  background: var(--accent);
  border-radius: 9px;
}
.pending__title {
  flex: none;
  font-weight: 600;
}
.pending__why {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending__list {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid color-mix(in srgb, var(--accent) 30%, var(--line-2));
}
.pending__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
}
.pending__row + .pending__row {
  border-top: 1px solid color-mix(in srgb, var(--accent) 20%, var(--line-2));
}
.pending__pin {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  margin-top: 1px;
  padding: 0 4px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: var(--accent-on);
  background: var(--accent);
  border-radius: 9px;
}
.pending__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pending__where {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.pending__where-icon {
  flex: none;
}
.pending__where-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending__quote {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending__text {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  word-break: break-word;
}
.pending__side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.pending__chip {
  padding: 1px 8px;
  font-size: 11px;
  line-height: 18px;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  border-radius: var(--radius-pill);
  white-space: nowrap;
}
.pending__x {
  display: inline-flex;
  padding: 2px;
  color: var(--muted);
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.pending__x:hover {
  color: var(--text);
  background: var(--fill);
}
.pending__hint {
  padding: 0 12px 8px;
}
.pending__error {
  margin: 0;
  padding: 0 12px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--danger-ink);
}
</style>
