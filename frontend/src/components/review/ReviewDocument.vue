<script setup lang="ts">
// 「改动」里打开的一份 Office 文件：左边是这一版，右边是它带的修订和批注。
//
// 批注指着文件自己的单位：Word 选中一段文字（连同第几页），幻灯片选中一页上的文字，
// 表格点一个单元格。指好了右边出现写批注的框，退回时和代码批注一起交给 AI 队友。
// 「对比」把左边换成和上一版的比较（`OfficeCompare`）：第一次交付比任务开始时的版本，
// 被退回过的比退回时那一版。
//
// 修订待审阅时只看不改：接受或拒绝会改动文件本身，哪一处不要，写一条批注。
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { FileKind } from '@/lib/fileKind'
import type { DiffReview, OfficeComparison, ReviewComment, ReviewCommentDraft } from '@/types/reviewComment'

import { computed, ref, watch } from 'vue'

import OfficeCompare from './OfficeCompare.vue'
import ReviewCommentBox from './ReviewCommentBox.vue'
import ReviewCommentCard from './ReviewCommentCard.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import PreviewPages from '@/components/panels/preview/PreviewPages.vue'
import PreviewSheet from '@/components/panels/preview/PreviewSheet.vue'
import PreviewSlides from '@/components/panels/preview/PreviewSlides.vue'
import RevisionList from '@/components/panels/preview/RevisionList.vue'
import { t } from '@/i18n'
import { placeKind, placeLabel } from '@/lib/reviewPlace'

const props = withDefaults(
  defineProps<{
    path: string
    documentType: FileKind | null
    docBytes: ArrayBuffer | null
    revs: DocumentRevisionsBundle
    revisionPath?: string | null
    review?: DiffReview | null
    /** 能不能和上一版比（任务里的 Word、表格、幻灯片）。 */
    canCompare?: boolean
    comparing?: boolean
    comparison?: OfficeComparison | null
    comparisonLoading?: boolean
    comparisonError?: string
  }>(),
  {
    revisionPath: null,
    review: null,
    canCompare: false,
    comparing: false,
    comparison: null,
    comparisonLoading: false,
    comparisonError: '',
  }
)

const emit = defineEmits<{
  (e: 'toggle-compare'): void
  (e: 'comment', draft: ReviewCommentDraft): void
  (e: 'edit-comment', id: string, body: string, suggestion: string | null): void
  (e: 'remove-comment', id: string): void
}>()

const kind = computed(() => placeKind(props.path))
const isSlides = computed(() => /\.(pptx?|odp)$/i.test(props.path))
const commentable = computed(() => kind.value !== 'line' && !!props.review?.writable)

// 指着的那一处：存下来的位置和那里的字。换了文件就作废。
const picked = ref<{ place: string; text: string; label: string } | null>(null)
watch(
  () => props.path,
  () => (picked.value = null)
)

function onQuote(payload: { text: string; page: number }) {
  if (!commentable.value || !payload.text.trim()) return
  const prefix = kind.value === 'slide' ? 's' : 'p'
  picked.value = {
    place: `${prefix}${payload.page}`,
    text: payload.text,
    label: t('work.room.review.page', { n: payload.page }),
  }
}
function onCell(payload: { address: string; value: string; sheet: string }) {
  if (!commentable.value || kind.value !== 'cell' || !payload.sheet) return
  const place = `${payload.sheet}!${payload.address}`
  picked.value = { place, text: payload.value, label: place }
}

function submit(body: string, suggestion: string | null) {
  const at = picked.value
  if (!at) return
  emit('comment', {
    path: props.path,
    line_start: 0,
    line_end: 0,
    line_text: at.text,
    place: at.place,
    body,
    suggestion,
  })
  picked.value = null
}

const mine = computed(() => (props.review?.comments ?? []).filter((c) => c.path === props.path))
const topLevel = computed(() => mine.value.filter((c) => !c.parent_id))
function repliesOf(id: string) {
  return mine.value.filter((c) => c.parent_id === id)
}
function reply(parent: ReviewComment, body: string) {
  emit('comment', {
    path: parent.path,
    line_start: parent.line_start,
    line_end: parent.line_end,
    line_text: parent.line_text,
    place: parent.place,
    body,
    suggestion: null,
    parent_id: parent.id,
  })
}

const compareLabel = computed(() =>
  props.comparison?.against === 'returned' ? t('work.room.review.compareReturned') : t('work.room.review.compareTaken')
)
</script>

<template>
  <div class="review-doc">
    <div v-if="canCompare" class="review-doc__bar">
      <span v-if="comparing && comparison" class="t-meta">{{ compareLabel }}</span>
      <span class="review-doc__grow" />
      <BaseButton
        kind="ghost"
        size="sm"
        prepend-icon="mdi-compare-horizontal"
        :aria-pressed="comparing"
        @click="emit('toggle-compare')"
      >
        {{ comparing ? t('work.room.review.compareBack') : t('work.room.review.compare') }}
      </BaseButton>
    </div>
    <div class="review-doc__main">
      <div class="review-doc__view">
        <template v-if="comparing">
          <div v-if="comparisonLoading" class="review-doc__state">
            <v-progress-circular indeterminate color="primary" size="24" />
          </div>
          <p v-else-if="comparisonError" class="t-meta review-doc__state">{{ comparisonError }}</p>
          <OfficeCompare v-else-if="comparison" :comparison="comparison" />
          <p v-else class="t-meta review-doc__state">{{ t('work.room.review.compareUnavailable') }}</p>
        </template>
        <template v-else>
          <PreviewSlides v-if="isSlides" :data="docBytes" @quote="onQuote" />
          <PreviewPages v-else-if="documentType?.view === 'pages'" :data="docBytes" @quote="onQuote" />
          <PreviewSheet v-else :data="docBytes" :kind="documentType?.sheet ?? 'workbook'" @cell="onCell" />
        </template>
      </div>
      <aside class="review-doc__side">
        <RevisionList :revs="revs" :path="revisionPath" />
        <section v-if="review && kind !== 'line'" class="review-doc__comments" data-testid="document-comments">
          <div class="review-doc__head" :title="commentable ? t('work.room.review.documentHint') : undefined">
            {{ t('work.room.review.comments', { n: topLevel.length }) }}
          </div>
          <div v-if="picked" class="review-doc__picked">
            <div class="t-meta">{{ t('work.room.review.pickedAt', { place: picked.label }) }}</div>
            <ReviewCommentBox
              :line-text="picked.text"
              :submit-label="t('work.room.review.add')"
              :busy="review.busy"
              @submit="submit"
              @cancel="picked = null"
            />
          </div>
          <ReviewCommentCard
            v-for="c in topLevel"
            :key="c.id"
            :comment="c"
            :replies="repliesOf(c.id)"
            :where="placeLabel(c)"
            :mine="c.author === review.me"
            :writable="review.writable"
            :agent-name="review.agentName"
            :busy="review.busy"
            @edit="(id, b, s) => emit('edit-comment', id, b, s)"
            @remove="(id) => emit('remove-comment', id)"
            @reply="(_id, body) => reply(c, body)"
          />
          <p v-if="!topLevel.length && !picked" class="t-meta review-doc__empty">
            {{ t('work.room.review.noComments') }}
          </p>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.review-doc {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.review-doc__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--line);
}
.review-doc__grow {
  flex: 1 1 auto;
}
.review-doc__main {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.review-doc__view {
  flex: 1 1 auto;
  min-width: 0;
  overflow: auto;
}
.review-doc__side {
  display: flex;
  flex: 0 0 320px;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 8px 0;
  overflow: auto;
  border-left: 1px solid var(--line);
}
.review-doc__comments {
  display: flex;
  flex-direction: column;
}
.review-doc__head {
  padding: 0 12px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}
.review-doc__picked {
  padding: 6px 12px 0;
}
.review-doc__picked :deep(.comment-box) {
  margin: 6px 0 8px;
}
.review-doc__empty {
  margin: 6px 12px;
}
.review-doc__state {
  display: flex;
  justify-content: center;
  padding: 24px 16px;
}
@media (max-width: 760px) {
  .review-doc__main {
    flex-direction: column;
  }
  .review-doc__side {
    flex-basis: auto;
    border-top: 1px solid var(--line);
    border-left: 0;
  }
}
</style>
