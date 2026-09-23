<script setup lang="ts">
import { useI18n } from 'vue-i18n'

/**
 * AdminQueueFoot.vue — 列表脚的**内容**，队列视图和总表视图各用一次。
 *
 * 两个视图各有一只脚（`AdminQueueList` 的 `.qlist__foot`、`AdminFeedbackTable` 的
 * `.aft__foot`），脚里的内容只有这一份：搜索口径注、行数、「已到底」、翻页两颗。
 * 以前总表没有脚插槽，页面只好在表外手画第二只脚 —— 同功能两份实现，样式迟早
 * 分叉；插槽补齐之后内容收进这里。
 *
 * **纯呈现，不碰 store**：翻页按下去报 `prev` / `next`，真的发请求的是页面 ——
 * 「队列本体拿不到写入口」的那条分工不在这里破。
 *
 * 根是 `display: contents`：壳的几何（40px、分隔线、padding）归宿主的插槽容器，
 * 这个组件只带内容，子元素直接进宿主的 flex 行。
 */

defineProps<{
  /** 搜索生效中（脚里要画「只搜哪几列」的口径注）。 */
  scope: boolean
  /** 手上这一页的行数。 */
  rows: number
  hasPrev: boolean
  hasNext: boolean
}>()

const emit = defineEmits<{
  (e: 'prev'): void
  (e: 'next'): void
}>()

const { t } = useI18n()
</script>

<template>
  <div class="qfoot">
    <span v-if="scope" class="qfoot__scope">{{ t('feedback.queue.search.scope') }}</span>
    <span class="qfoot__spacer" />
    <span class="qfoot__count t-num">{{ t('feedback.queue.foot.rows', { n: rows }) }}</span>
    <span v-if="!hasNext" class="qfoot__end">· {{ t('feedback.queue.foot.end') }}</span>
    <button v-if="hasPrev" type="button" class="qfoot__pager" @click="emit('prev')">
      {{ t('feedback.queue.pager.prev') }}
    </button>
    <button v-if="hasNext" type="button" class="qfoot__pager" @click="emit('next')">
      {{ t('feedback.queue.pager.next') }}
    </button>
  </div>
</template>

<style scoped>
.qfoot {
  display: contents;
}

.qfoot__scope {
  flex: 0 1 auto;
  overflow: hidden;
  min-width: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qfoot__spacer {
  flex: 1 1 auto;
}

.qfoot__count,
.qfoot__end {
  flex: 0 0 auto;
  white-space: nowrap;
}

.qfoot__pager {
  flex: 0 0 auto;
  height: 24px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.qfoot__pager:hover {
  background: var(--fill);
}

.qfoot__count {
  margin-left: 4px;
}
</style>
