<script setup lang="ts">
// 正文上方那一条：修改建议还有几处，上一处 / 下一处，一次全部决定。
// 全部处理完了，说一声各接受、拒绝了几处。
//
// 建议是人提的还是 AI 队友提的都有可能。全出自一个人时写那个人的名字；混着
// 几个人时不写名字，只说是修改建议。
import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{
  /** 待处理的建议全是同一个人的时那个人的名字；混着几个人时是 null。 */
  author: string | null
  /** 刚处理完的这一批建议全是同一个人的时那个人的名字；混着几个人时是 null。 */
  decidedAuthor: string | null
  /** 还有几处待处理。 */
  count: number
  /** 这一次自己决定了几处；都处理完时说出来。 */
  decided: { accepted: number; rejected: number }
  /** 没有编辑权限时只能看。 */
  editable: boolean
}>()
const emit = defineEmits<{
  (e: 'step', direction: -1 | 1): void
  (e: 'accept-all'): void
  (e: 'reject-all'): void
  (e: 'dismiss'): void
}>()
</script>

<template>
  <div
    v-if="count > 0"
    class="doc-suggestion-strip"
    role="region"
    :aria-label="author ? t('work.room.docSuggest.title', { author }) : t('work.room.docSuggest.titleMixed')"
  >
    <span class="doc-suggestion-strip__lead">
      <span class="doc-suggestion-strip__dot" aria-hidden="true" />
      <span class="doc-suggestion-strip__text">{{
        author
          ? t('work.room.docSuggest.pending', { author, n: count })
          : t('work.room.docSuggest.pendingMixed', { n: count })
      }}</span>
      <button
        type="button"
        class="doc-suggestion-strip__nav"
        :aria-label="t('work.room.docEdit.prev')"
        :title="t('work.room.docEdit.prev')"
        @click="emit('step', -1)"
      >
        <v-icon size="16">mdi-arrow-up</v-icon>
      </button>
      <button
        type="button"
        class="doc-suggestion-strip__nav"
        :aria-label="t('work.room.docEdit.next')"
        :title="t('work.room.docEdit.next')"
        @click="emit('step', 1)"
      >
        <v-icon size="16">mdi-arrow-down</v-icon>
      </button>
    </span>
    <span v-if="editable" class="doc-suggestion-strip__actions">
      <DocEditButton @click="emit('reject-all')">{{ t('work.room.docSuggest.rejectAll') }}</DocEditButton>
      <DocEditButton strong @click="emit('accept-all')">{{ t('work.room.docSuggest.acceptAll') }}</DocEditButton>
    </span>
  </div>
  <div
    v-else-if="decided.accepted + decided.rejected > 0"
    class="doc-suggestion-strip doc-suggestion-strip--done"
    role="status"
  >
    <span class="doc-suggestion-strip__text">{{
      decidedAuthor
        ? t('work.room.docSuggest.allDone', {
            author: decidedAuthor,
            accepted: decided.accepted,
            rejected: decided.rejected,
          })
        : t('work.room.docSuggest.allDoneMixed', {
            accepted: decided.accepted,
            rejected: decided.rejected,
          })
    }}</span>
    <span class="doc-suggestion-strip__spacer" />
    <button
      type="button"
      class="doc-suggestion-strip__nav"
      :aria-label="t('work.room.docSuggest.dismiss')"
      :title="t('work.room.docSuggest.dismiss')"
      @click="emit('dismiss')"
    >
      <v-icon size="16">mdi-close</v-icon>
    </button>
  </div>
</template>

<style scoped>
.doc-suggestion-strip {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 6px 16px;
  border-bottom: 1px solid color-mix(in srgb, var(--ok) 24%, transparent);
  background: var(--ok-wash);
}
.doc-suggestion-strip--done {
  border-bottom-color: var(--line);
  background: var(--surface);
}
.doc-suggestion-strip__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-pill);
  background: var(--ok);
}
.doc-suggestion-strip__text {
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-suggestion-strip--done .doc-suggestion-strip__text {
  color: var(--muted);
}
.doc-suggestion-strip__spacer {
  flex: 1 1 auto;
}
/* 窄屏上按钮整组折到下一行、靠右，不把一组按钮拆开。 */
.doc-suggestion-strip__lead,
.doc-suggestion-strip__actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.doc-suggestion-strip__actions {
  margin-left: auto;
}
.doc-suggestion-strip__nav {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-suggestion-strip__nav:hover {
  background: var(--fill);
}
.doc-suggestion-strip__nav:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
