<script setup lang="ts">
// 总览的其余两块（#1889 ②③）—— 排在文档正文下面，不在编辑器里。
//
// 项目总览是三块：①「项目是什么」是文档正文，人 / AI 队友写；②③ 由平台从话题和
// 结论现拼。在这之前，这两块只进 AI 队友的提示词，人翻开总览文档只看得到
// 一小部分——而「这个项目现在在做什么」正是人打开总览要看的东西。
//
// 它给的是「去哪看」，不是一段死文字：每一条都点得动，进那个话题的房间。所以每一条
// 都带着自己的 id。
//
// 两个读者，一份来源：注入提示词的那份 markdown 和这里读的是后端同一次取数
// （`TopicService.overview_auto_data`），所以人和 AI 队友读到的不会各说各的。
//
// 空块不画，也不补「（暂无）」：后端就不下发空块。整段拿不到也不补报错以外的东西
// ——它是正文之外的一栏，正文还在。
//
// 这一段也不自己取数：两块的内容由文档那一格的取数带着来（`composables/usePanelDoc.ts`
// 读，`components/work/PanelDocHost.vue` / `PanelOverviewHost.vue` 接线），这里只画。
import type { OverviewAutoBlock, OverviewAutoItem } from '../../../cx_types'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 平台现拼的那两块；空数组就是没有这两块。 */
    blocks?: OverviewAutoBlock[]
    /** 那一次没读回来：照实说，并给一个重试。 */
    failed?: boolean
    /** 重读一次。 */
    reload?: () => void
  }>(),
  { blocks: () => [], failed: false, reload: undefined }
)

const emit = defineEmits<{
  /** 一条话题：进那个房间。 */
  (e: 'open-topic', topicId: string): void
}>()

function retry() {
  props.reload?.()
}

// 逐条渲染成同一行形状：主要那句话 + 一行元信息（谁在做、做到哪了）。
// 两个话题条目可能同名，所以 key 用 id。
function keyOf(item: OverviewAutoItem): string {
  return `t-${item.topic_id}`
}

// 话题那一行的元信息：谁在做 · 做到哪了。两样都没有就不画这一格。
function topicMeta(item: OverviewAutoItem): string {
  return [item.owner, item.status].filter(Boolean).join(' · ')
}

function open(item: OverviewAutoItem) {
  emit('open-topic', item.topic_id)
}
</script>

<template>
  <section v-if="blocks.length || failed" class="overview-auto">
    <!-- 拿不到就照实说，不装作这两块本来就没有：这一栏正是「项目全局」唯一的
         落点，静默消失的后果和人从没读到它一样。 -->
    <div v-if="failed" class="overview-auto__failed">
      <span class="t-body c-muted">{{ t('work.room.overviewAuto.failed') }}</span>
      <button type="button" class="overview-auto__retry t-body" @click="retry">
        {{ t('work.room.retry.action') }}
      </button>
    </div>

    <div v-for="block in blocks" :key="block.key" class="auto-block">
      <div class="auto-block__head">
        <h2 class="auto-block__title">{{ block.title }}</h2>
        <span class="auto-block__badge t-meta-read">{{ t('work.room.overviewAuto.badge') }}</span>
      </div>
      <ul class="auto-block__items">
        <li v-for="item in block.items" :key="keyOf(item)">
          <button type="button" class="auto-item" @click="open(item)">
            <span class="auto-item__row">
              <span class="auto-item__title">{{ item.title }}</span>
              <span v-if="topicMeta(item)" class="auto-item__meta">{{ topicMeta(item) }}</span>
            </span>
            <!-- 结论 / 出处另起一行：它是「这条现在是什么」，和上面那行去处不是
                 一件事，挤在一行里两句话会互相盖住。 -->
            <span v-if="item.conclusion" class="auto-item__sub">
              {{ item.conclusion }}
            </span>
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
/* 和正文同一条栏宽、同一个左边缘：它是文档的一部分，不是文档边上的另一栏。 */
.overview-auto {
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 720px;
  margin: 32px auto 0;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.overview-auto__failed {
  display: flex;
  align-items: center;
  gap: 12px;
}
.overview-auto__retry {
  padding: 0;
  cursor: pointer;
  color: var(--accent-ink);
}
.overview-auto__retry:hover {
  text-decoration: underline;
}
.auto-block__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
/* 和正文里的 ## 同一档（.doc-editor h2 是 15/600）：三块读起来是文档的续篇。 */
.auto-block__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15-reading);
  color: var(--ink);
}
.auto-block__badge {
  margin-left: auto;
}
.auto-block__items {
  margin: 0;
  padding: 0;
  list-style: none;
}
.auto-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  /* 悬停底色要盖住整行，却又不能把文字推得比正文更靠右：负边距配等量内边距。 */
  margin: 0 -8px;
  padding: 4px 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.auto-item:hover {
  background: var(--fill);
}
.auto-item__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.auto-item__title {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}
.auto-item__meta {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--faint);
  font-size: 12.5px;
  line-height: var(--lh-12);
}
.auto-item__sub {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
