<script setup lang="ts">
// 概览里的「章节」：一段对话的目录，按对话里的顺序编号。点一条回到那一节；说得上话的
// 人在 ⋯ 里取消。没有章节就整段不画（入口在消息的 ⋯ 里，概览不占地方教育人）。
import type { MenuAction } from '@/components/common/menuAction'
import type { RefNames } from '@/lib/refChip'
import type { ChapterRow } from '@/types/chapters'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { replySnippet } from '@/lib/blockDisplay'
import { inReadingOrder } from '@/lib/chapters'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  chapters: ChapterRow[]
  /** 能不能取消章节：在主线说得上话的人（任务里是项目里的人）。 */
  canChapter: boolean
  memberNames: Record<string, string>
  refs: RefNames
}>()

const emit = defineEmits<{
  (e: 'jump', blockId: string): void
  (e: 'unpin', blockId: string): void
}>()

const ordered = computed(() => inReadingOrder(props.chapters))
const nameOf = (handle: string) => props.memberNames[handle] || handle

function titleOf(chapter: ChapterRow): string {
  return replySnippet(chapter.block, props.refs, 80) || t('work.channel.chapters.untitled')
}

function actions(chapter: ChapterRow): MenuAction[] {
  const out: MenuAction[] = [
    {
      key: 'jump',
      label: t('work.channel.chapters.jump'),
      icon: 'mdi-arrow-right',
      onSelect: () => emit('jump', chapter.block.id),
    },
  ]
  if (props.canChapter)
    out.push({
      key: 'unpin',
      label: t('work.channel.chapters.unpin'),
      icon: 'mdi-bookmark-minus-outline',
      onSelect: () => emit('unpin', chapter.block.id),
    })
  return out
}
</script>

<template>
  <section v-if="ordered.length" class="chapters" data-testid="channel-chapters">
    <h3 class="chapters__title">{{ t('work.channel.chapters.title', { count: ordered.length }) }}</h3>
    <div v-for="(chapter, i) in ordered" :key="chapter.block.id" class="chapter">
      <button
        type="button"
        class="chapter__body"
        :aria-label="titleOf(chapter)"
        @click="emit('jump', chapter.block.id)"
      >
        <span class="chapter__no" aria-hidden="true">{{ i + 1 }}</span>
        <span class="chapter__text">
          <span class="t-body chapter__name">{{ titleOf(chapter) }}</span>
          <span class="t-meta c-faint">{{
            t('work.channel.chapters.pinnedBy', { name: nameOf(chapter.pinned_by), when: relTime(chapter.pinned_at) })
          }}</span>
        </span>
      </button>
      <AdaptiveMenu :actions="actions(chapter)" :title="t('work.channel.chapters.menu')">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="sm"
            class="tap-target"
            :aria-label="t('work.channel.chapters.menu')"
          />
        </template>
      </AdaptiveMenu>
    </div>
  </section>
</template>

<style scoped>
.chapters {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chapters__title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.chapter {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 4px 4px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.chapter__body {
  display: flex;
  flex: 1 1 auto;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 4px 0;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.chapter__no {
  flex: none;
  min-width: 18px;
  padding-top: 1px;
  font-size: 13px;
  line-height: var(--lh-13);
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
.chapter__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.chapter__name {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  color: var(--text);
}
</style>
