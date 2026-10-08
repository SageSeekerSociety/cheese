<script setup lang="ts">
// 一条批注，挂在它说的那几行下面。
//
// 自己没送出的那条能改、能删；上一轮送出的那条带着芝士说的处理结果，待审阅时能回复，
// 回复下次退回时一起带上。修改建议画成一小段差异：原来的几行和建议的几行。
import type { ReviewComment } from '@/types/reviewComment'

import { computed, ref } from 'vue'

import ReviewCommentBox from './ReviewCommentBox.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    comment: ReviewComment
    replies?: ReviewComment[]
    /** 写在哪：「第 244 行」「原位置已删除」。 */
    where: string
    /** 看的人自己写的。 */
    mine: boolean
    /** 待审阅：能回复上一轮的批注。 */
    writable: boolean
    agentName: string
    busy?: boolean
  }>(),
  { replies: () => [], busy: false }
)

const emit = defineEmits<{
  (e: 'edit', id: string, body: string, suggestion: string | null): void
  (e: 'remove', id: string): void
  (e: 'reply', parentId: string, body: string): void
}>()

const editing = ref<string | null>(null)
const replying = ref(false)

const draft = computed(() => props.comment.state === 'draft')
const tag = computed<{ label: string; tone: 'plain' | 'ok' | 'warn' }>(() => {
  const c = props.comment
  if (c.state === 'draft') return { label: t('work.room.review.unsent'), tone: 'plain' }
  if (c.outcome === 'handled') return { label: t('work.room.review.handled'), tone: 'ok' }
  if (c.outcome === 'not_handled') return { label: t('work.room.review.notHandled'), tone: 'warn' }
  return { label: t('work.room.review.unanswered'), tone: 'plain' }
})
const title = computed(() => {
  if (draft.value)
    return props.comment.suggestion !== null ? t('work.room.review.mySuggestion') : t('work.room.review.myComment')
  return t('work.room.review.lastRound')
})
const oldLines = computed(() => props.comment.line_text.split('\n').filter((l, i, a) => i < a.length - 1 || l !== ''))
const newLines = computed(() =>
  (props.comment.suggestion ?? '').split('\n').filter((l, i, a) => i < a.length - 1 || l !== '')
)

function saveEdit(id: string, body: string, suggestion: string | null) {
  emit('edit', id, body, suggestion)
  editing.value = null
}
function sendReply(body: string) {
  emit('reply', props.comment.id, body)
  replying.value = false
}
</script>

<template>
  <div class="comment-card" :class="{ 'comment-card--sent': !draft }">
    <div class="comment-card__head">
      <span class="comment-card__title">{{ title }}</span>
      <span class="comment-card__tag" :class="`comment-card__tag--${tag.tone}`">{{ tag.label }}</span>
      <span class="t-meta">{{ where }}</span>
    </div>
    <ReviewCommentBox
      v-if="editing === comment.id"
      :line-text="comment.line_text"
      :body="comment.body"
      :suggestion="comment.suggestion"
      :submit-label="t('work.room.review.save')"
      :busy="busy"
      @submit="(b, s) => saveEdit(comment.id, b, s)"
      @cancel="editing = null"
    />
    <template v-else>
      <p v-if="comment.body" class="comment-card__body">{{ comment.body }}</p>
      <div
        v-if="comment.suggestion !== null"
        class="comment-card__suggestion"
        :aria-label="t('work.room.review.suggestion')"
      >
        <div v-for="(l, i) in oldLines" :key="`d${i}`" class="comment-card__line comment-card__line--del">
          <span aria-hidden="true">−</span><span>{{ l }}</span>
        </div>
        <div v-for="(l, i) in newLines" :key="`a${i}`" class="comment-card__line comment-card__line--add">
          <span aria-hidden="true">+</span><span>{{ l }}</span>
        </div>
      </div>
      <p v-if="comment.outcome_note" class="t-meta comment-card__answer">
        {{ t('work.room.review.answer', { agent: agentName, note: comment.outcome_note }) }}
      </p>
    </template>
    <div v-for="r in replies" :key="r.id" class="comment-card__reply">
      <ReviewCommentBox
        v-if="editing === r.id"
        :body="r.body"
        :allow-suggestion="false"
        :submit-label="t('work.room.review.save')"
        :busy="busy"
        @submit="(b) => saveEdit(r.id, b, null)"
        @cancel="editing = null"
      />
      <template v-else>
        <p class="comment-card__body">{{ r.body }}</p>
        <div v-if="r.state === 'draft'" class="comment-card__actions">
          <span class="comment-card__tag comment-card__tag--plain">{{ t('work.room.review.unsent') }}</span>
          <BaseButton kind="ghost" size="sm" @click="editing = r.id">{{ t('work.room.review.edit') }}</BaseButton>
          <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('remove', r.id)">
            {{ t('work.room.review.delete') }}
          </BaseButton>
        </div>
      </template>
    </div>
    <ReviewCommentBox
      v-if="replying"
      :allow-suggestion="false"
      :submit-label="t('work.room.review.reply')"
      :busy="busy"
      @submit="(b) => sendReply(b)"
      @cancel="replying = false"
    />
    <div v-if="editing !== comment.id && !replying" class="comment-card__actions">
      <template v-if="draft && mine">
        <BaseButton kind="ghost" size="sm" @click="editing = comment.id">{{ t('work.room.review.edit') }}</BaseButton>
        <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('remove', comment.id)">
          {{ t('work.room.review.delete') }}
        </BaseButton>
      </template>
      <BaseButton v-else-if="!draft && writable" kind="ghost" size="sm" @click="replying = true">
        {{ t('work.room.review.reply') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.comment-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 6px 12px 8px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  font-family: var(--font-sans);
  white-space: normal;
}
.comment-card__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.comment-card__title {
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}
.comment-card__tag {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  line-height: var(--lh-12);
}
.comment-card__tag--plain {
  background: var(--fill);
  color: var(--muted);
}
.comment-card__tag--ok {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.comment-card__tag--warn {
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.comment-card__body {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.comment-card__suggestion {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.comment-card__line {
  display: flex;
  gap: 8px;
  padding: 0 8px;
  white-space: pre-wrap;
}
.comment-card__line--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.comment-card__line--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.comment-card__answer {
  margin: 0;
}
.comment-card__reply {
  padding-left: 10px;
  border-left: 2px solid var(--line);
}
.comment-card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
