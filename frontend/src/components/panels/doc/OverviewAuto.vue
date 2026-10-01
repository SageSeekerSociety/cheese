<script setup lang="ts">
// 总览的其余三块（#1889 ②~④）—— 排在文档正文下面，不在编辑器里。
//
// 项目总览是四块：①「项目是什么」是文档正文，人 / AI 队友写；②~④ 由平台从话题、
// 里程碑、结论现拼。在这之前，这三块只进 AI 队友的提示词，人翻开总览文档只看得到
// 一小部分——而「这个项目现在在做什么」正是人打开总览要看的东西。
//
// 它给的是「去哪看」，不是一段死文字：每一条都点得动，去它自己的那一头——话题进那
// 个房间，里程碑进日历。所以每一条都带着自己的 id。
//
// 两个读者，一份来源：注入提示词的那份 markdown 和这里读的是后端同一次取数
// （`TopicService.overview_auto_data`），所以人和 AI 队友读到的不会各说各的。
//
// 空块不画，也不补「（暂无）」：后端就不下发空块。整段拿不到也不补报错以外的东西
// ——它是正文之外的一栏，正文还在。
import type { OverviewAutoBlock, OverviewAutoItem, Topic } from '../../../cx_types'

import { ref, watch } from 'vue'

import { getOverviewAuto } from '../../../api'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 每有一轮动静就加一：话题状态、里程碑都可能变了。 */
    activityTick?: number
  }>(),
  { activityTick: 0 }
)

const emit = defineEmits<{
  /** 一条话题：进那个房间。 */
  (e: 'open-topic', topicId: string): void
  /**
   * 里程碑有自己的一页，不在这间房里——交给拿着路由的那一层
   * （`TopicView.handleOpenResource`），面板自己不导航。
   */
  (e: 'open-resource', resource: 'milestone'): void
}>()

const blocks = ref<OverviewAutoBlock[]>([])
const failed = ref(false)

async function load() {
  const tid = props.topic?.id
  if (!tid) {
    blocks.value = []
    failed.value = false
    return
  }
  try {
    blocks.value = (await getOverviewAuto(tid)).blocks ?? []
    failed.value = false
  } catch {
    blocks.value = []
    failed.value = true
  }
}

void load()
watch(
  () => [props.topic?.id, props.activityTick],
  () => void load()
)

// 里程碑的三个状态：接口给的是原值（upcoming / done / missed），怎么说是界面的事。
function milestoneLabel(status: string | null): string {
  if (status === 'done') return t('work.room.overviewAuto.milestone.done')
  if (status === 'missed') return t('work.room.overviewAuto.milestone.missed')
  return t('work.room.overviewAuto.milestone.upcoming')
}

// 状态用点，不用彩色标签（设计系统 §1.5）：迟到的那条才是要点出来的一件。
function milestoneDot(status: string | null): string {
  if (status === 'done') return 'status-dot--ok'
  if (status === 'missed') return 'status-dot--danger'
  return 'status-dot--muted'
}

function dueLabel(due: string | null): string {
  return due
    ? t('work.room.overviewAuto.milestone.due', { date: due.slice(0, 10) })
    : t('work.room.overviewAuto.milestone.noDue')
}

// 逐条渲染成同一行形状：主要那句话 + 一行元信息（去哪儿、什么状态、什么时候）。
// 两个话题条目可能同名，所以 key 用 id；id 是后端来的，理论上一定在。
function keyOf(item: OverviewAutoItem, index: number): string {
  if (item.kind === 'topic') return `t-${item.topic_id}`
  return `m-${item.milestone_id ?? index}`
}

// 话题那一行的元信息：谁在做 · 做到哪了。两样都没有就不画这一格。
function topicMeta(item: OverviewAutoItem): string {
  if (item.kind !== 'topic') return ''
  return [item.owner, item.status].filter(Boolean).join(' · ')
}

function open(item: OverviewAutoItem) {
  if (item.kind === 'topic') {
    emit('open-topic', item.topic_id)
    return
  }
  emit('open-resource', 'milestone')
}
</script>

<template>
  <section v-if="blocks.length || failed" class="overview-auto">
    <!-- 拿不到就照实说，不装作这三块本来就没有：这一栏正是「项目全局」唯一的
         落点，静默消失的后果和人从没读到它一样。 -->
    <div v-if="failed" class="overview-auto__failed">
      <span class="t-body c-muted">{{ t('work.room.overviewAuto.failed') }}</span>
      <button type="button" class="overview-auto__retry t-body" @click="load">
        {{ t('work.room.retry.action') }}
      </button>
    </div>

    <div v-for="block in blocks" :key="block.key" class="auto-block">
      <div class="auto-block__head">
        <h2 class="auto-block__title">{{ block.title }}</h2>
        <span class="auto-block__badge t-meta-read">{{ t('work.room.overviewAuto.badge') }}</span>
      </div>
      <ul class="auto-block__items">
        <li v-for="(item, index) in block.items" :key="keyOf(item, index)">
          <button type="button" class="auto-item" @click="open(item)">
            <span class="auto-item__row">
              <span v-if="item.kind === 'milestone'" class="status-dot" :class="milestoneDot(item.status)" />
              <span class="auto-item__title">{{ item.title }}</span>
              <span v-if="item.kind === 'milestone'" class="auto-item__meta">
                {{ milestoneLabel(item.status) }} · {{ dueLabel(item.due) }}
              </span>
              <span v-else-if="topicMeta(item)" class="auto-item__meta">{{ topicMeta(item) }}</span>
            </span>
            <!-- 结论 / 出处另起一行：它是「这条现在是什么」，和上面那行去处不是
                 一件事，挤在一行里两句话会互相盖住。 -->
            <span v-if="item.kind === 'topic' && item.conclusion" class="auto-item__sub">
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
/* 和正文里的 ## 同一档（.doc-editor h2 是 15/600）：四块读起来是文档的续篇。 */
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
