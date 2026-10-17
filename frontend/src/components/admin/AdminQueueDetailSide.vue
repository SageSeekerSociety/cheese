<script setup lang="ts">
import type { AdminCandidate } from '@/api'
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminAssigneeSelect from '@/components/admin/AdminAssigneeSelect.vue'
import StatusRail from '@/components/admin/StatusRail.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { allStatuses, KIND_LABEL, PRIORITY_META, SOURCE_LABEL, statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

/**
 * AdminQueueDetailSide.vue — 详情左栏：返回、身份、分诊面板、事实、时间线。
 *
 * 从 `AdminQueueDetail.vue` 拆出来的（那一件原来 949 行，顶到 `frontend/src` 的行数
 * 上限）。切法是「哪一栏」：左右两栏各自成件，父组件只剩两栏的版式和底下那根操作栏。
 * 窄屏时这一件在上面、宽屏时它是左边那 440px —— 那两种形态是**父组件的一条媒体查询**，
 * 不是两个组件；所以 `.qdet__side` 自己的盒子（`display: flex`、背景、那 440px）留在
 * 父组件的 `<style>` 里，这里只管栏里面的东西。
 *
 * **只吃 props，写操作全部报出去**：状态是 `triage`、指派是 `assign`、优先级是
 * `priority`、搜索词是 `search`。这一件不认识 store 也不认识路由，所以能单独摆在预览站
 * 里看（目录条目见 `views/demo/catalogQueue.ts`）。
 */
defineOptions({ name: 'AdminQueueDetailSide' })

const props = withDefaults(
  defineProps<{
    item: FeedbackDetail | null
    /** 分诊面板展开着没有（§8 的 Esc 逐层后退、§9 验收第 23 条）。收起时这一块只留一行
     *  只读摘要（状态 · 指派 · 优先级），展开才是那几个控件；小屏上这一块占的是**纵向**
     *  空间，展开着会把正文挤到第二屏去。收起的默认值和这个状态本身都归调用方 ——
     *  `Esc` 要「一次只退一层」，而那一层的顺序由谁拿着详情谁决定，这里的按钮只把用户的
     *  意图报上去。 */
    triageOpen: boolean
    /** 服务端 meta 里那份状态梯子。缺省时用本地那一份。 */
    statuses?: FeedbackStatus[]
    /** 「指派给谁」那个框：候选与那串搜索词都归调用方（关详情时它得能清掉）。 */
    candidates?: AdminCandidate[]
    search?: string
    searching?: boolean
  }>(),
  { statuses: undefined, candidates: () => [], search: '', searching: false }
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
  (e: 'assign', id: string, handle: string | null): void
  (e: 'priority', id: string, priority: FeedbackPriority): void
  (e: 'search', v: string): void
}>()

const { t } = useI18n()

/** 状态选项用**服务端**那份（meta 里的 `statuses`）：梯子四级，外加梯子之外的
 *  「不修复」。
 *
 *  这几格在模板里**竖着排、每一格是一颗按钮**：队列页的 `1/2/3` 三个键对应的就是这几
 *  行，横排的话键位和位置对不上。当前那一格不画按钮（它就是现状），只标出来。 */
const statusItems = computed(() => allStatuses(props.statuses).map((s) => ({ title: statusMeta(s).label, value: s })))

const priorityItems = computed(() =>
  Object.entries(PRIORITY_META).map(([value, meta]) => ({ title: meta.label, value }))
)

const status = computed(() => statusMeta(props.item?.status))
const priority = computed(() => (props.item?.priority ? PRIORITY_META[props.item.priority] : null))

/** 收起时那一行摘要要说的三件事。用 `·` 连接的空值一律不画，免得出现「已修复 · · 高」。 */
const summary = computed(() => {
  const item = props.item
  if (!item) return ''
  return [
    status.value.label,
    item.assignee_handle ?? t('feedback.queueDetail.unassigned'),
    priority.value ? t('feedback.queueDetail.priorityValue', { label: priority.value.label }) : '',
  ]
    .filter(Boolean)
    .join(' · ')
})

const assignWrap = ref<HTMLElement | null>(null)

/** `A` 键要落到的那个输入框。按类名进去找（组件之间的契约里没有「把 input 交出来」这一
 *  项，而 `.v-field__input` 是 Vuetify 自己的、不是我们两家的私约）。父组件把它原样
 *  暴露给队列页 —— 这正是 `defineExpose` 在这一件上也想要的原因。 */
function focusAssignee() {
  assignWrap.value?.querySelector<HTMLInputElement>('input')?.focus()
}

defineExpose({ focusAssignee })

/* 模板里那几处讲究，以前是模板里的 HTML 注释，现在写在这儿：新文件的中文 HTML 注释过不了
   `lint:i18n`（那道闸门只放开存量，理由是注释会留在 vitest 的 DOM 里，被「渲染结果里不该
   有中文」的那几条用例读到），而中文的解释又不该翻成英文 —— 所以归到 `<script>` 块里。
 *
 *   · 「指派给谁」和「优先级」两个框的标签画在**框外**（设计规则）。优先级那颗 `<label>`
 *     的 `id` 得是「控件 id + `-label`」，Vuetify 的输入框认的就是它（见 BaseField）；
 *     `label-has-for` 默认要「套住控件」和「`for` 指过去」两样都有，而控件是 `v-select`
 *     组件、它看不见组件里那个 `input` —— `for`/`id` 这一对已经把它们连上了，所以模板里
 *     留着那一行 eslint-disable。 */
</script>

<template>
  <aside class="qdet__side">
    <div class="qdet__side-head">
      <button type="button" class="qdet__back" @click="emit('close')">
        <v-icon icon="mdi-arrow-left" size="16" aria-hidden="true" />
        {{ t('feedback.queueDetail.back') }}
      </button>
    </div>

    <div v-if="item" class="qdet__ident">
      <StatusRail :status="item.status" />
      <div class="qdet__ident-text">
        <div class="qdet__ident-top">
          <span class="qdet__word" :style="{ color: status.ink }">{{ status.label }}</span>
          <span class="t-meta-read t-num">{{ item.display_id }}</span>
        </div>
        <h2 class="qdet__title">{{ item.title }}</h2>
        <div class="t-meta-read t-num">{{ t('feedback.queue.supports', { n: item.supports }) }}</div>
      </div>
    </div>

    <section v-if="item" class="qdet__block">
      <div class="qdet__block-head">
        <span class="t-eyebrow-read">{{ t('feedback.queueDetail.triage') }}</span>
        <button v-if="!triageOpen" type="button" class="qdet__toggle" @click="emit('update:triageOpen', true)">
          {{ t('feedback.queueDetail.expand') }}
        </button>
        <button v-else type="button" class="qdet__toggle" @click="emit('update:triageOpen', false)">
          {{ t('feedback.queueDetail.collapse') }}
        </button>
      </div>

      <p v-if="!triageOpen" class="qdet__summary t-body">{{ summary }}</p>

      <div v-else class="qdet__fields">
        <div class="qdet__field">
          <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.status') }}</div>
          <div class="qdet__ladder" role="radiogroup" :aria-label="t('feedback.queueDetail.status')">
            <button
              v-for="s in statusItems"
              :key="s.value"
              type="button"
              role="radio"
              class="qdet__rung"
              :class="{ 'qdet__rung--on': item.status === s.value }"
              :aria-checked="item.status === s.value"
              @click="item.status !== s.value && emit('triage', s.value)"
            >
              {{ s.title }}
            </button>
          </div>
        </div>

        <div class="qdet__field">
          <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.assign') }}</div>
          <div ref="assignWrap">
            <AdminAssigneeSelect
              :model-value="item.assignee_handle"
              :label="t('feedback.queueDetail.assignTo')"
              :search="props.search"
              :candidates="props.candidates"
              :searching="props.searching"
              dense
              @update:model-value="(v: string | null) => emit('assign', item!.id, v)"
              @update:search="(v: string) => emit('search', v)"
            />
          </div>
        </div>

        <div class="qdet__field">
          <!-- eslint-disable-next-line vuejs-accessibility/label-has-for -->
          <label id="qdet-priority-label" for="qdet-priority" class="t-eyebrow-read qdet__label">{{
            t('feedback.queueDetail.priority')
          }}</label>
          <v-select
            id="qdet-priority"
            :model-value="item.priority"
            :items="priorityItems"
            autocomplete="off"
            density="compact"
            hide-details
            @update:model-value="(v: FeedbackPriority) => emit('priority', item!.id, v)"
          />
        </div>
      </div>
    </section>

    <section v-if="item" class="qdet__block">
      <div class="qdet__facts">
        <div class="qdet__fact">
          <span class="t-meta-read">{{ t('feedback.queueDetail.author') }}</span>
          <span class="qdet__fact-value">
            <FeedbackAuthorAvatar
              :handle="item.author_handle"
              :is-agent="item.author_is_agent"
              :avatar-id="item.author_avatar_id"
              :size="20"
            />
            {{ item.author_handle }}
          </span>
        </div>
        <div class="qdet__fact">
          <span class="t-meta-read">{{ t('feedback.queueDetail.created') }}</span>
          <span class="qdet__fact-value">{{ relTime(item.created_at) }}</span>
        </div>
        <div class="qdet__fact">
          <span class="t-meta-read">{{ t('feedback.queueDetail.lastActivity') }}</span>
          <span class="qdet__fact-value">{{ relTime(item.last_activity_at ?? item.created_at) }}</span>
        </div>
        <div class="qdet__fact">
          <span class="t-meta-read">{{ t('feedback.queueDetail.source') }}</span>
          <span class="qdet__fact-value">{{ SOURCE_LABEL[item.author_is_agent ? 'agent' : 'user'] }}</span>
        </div>
        <div class="qdet__fact">
          <span class="t-meta-read">{{ t('feedback.queueDetail.kind') }}</span>
          <span class="qdet__fact-value">{{ KIND_LABEL[item.kind] }}</span>
        </div>
      </div>
    </section>

    <section v-if="item" class="qdet__block">
      <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.timeline') }}</div>
      <ol class="qdet__timeline">
        <li v-for="(entry, i) in item.timeline" :key="`${entry.at}-${i}`" class="qdet__tl-row">
          <span class="status-dot" :style="{ background: statusMeta(entry.status).dot }" aria-hidden="true" />
          <span class="qdet__tl-word">{{ statusMeta(entry.status).label }}</span>
          <span class="t-meta-read t-num">{{ relTime(entry.at) }}</span>
        </li>
      </ol>
    </section>
  </aside>
</template>

<style scoped>
/* 这一件的根节点 `.qdet__side` 自己**不在这里画**：它是窄屏的上一段、宽屏左边那
   440px，两种形态是父组件的一条媒体查询，盒子那几条规则跟着那条决策一起留在父组件
   里（`AdminQueueDetail.vue` 的 `<style>`）。这里只管栏里面的东西。 */
.qdet__side-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  height: 56px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line-2);
}

/* 返回是一个次级动作：中性文字 + 图标，`--fill` 底只在 hover 时出现。 */
.qdet__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qdet__back:hover {
  color: var(--text);
  background: var(--fill);
}

.qdet__ident {
  display: flex;
  flex: 0 0 auto;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}

.qdet__ident-text {
  min-width: 0;
}

.qdet__ident-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.qdet__word {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}

/* 标题 18 / --lh-18 26（§4.4 左栏）。 */
.qdet__title {
  margin: 4px 0;
  color: var(--ink);
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
}

.qdet__block {
  flex: 0 0 auto;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}

.qdet__block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.qdet__toggle {
  height: 24px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition: background-color 0.12s ease;
}

.qdet__toggle:hover {
  background: var(--fill);
}

.qdet__summary {
  margin: 8px 0 0;
  color: var(--muted);
}

.qdet__fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

/* 栏位标签那一行：这一件和右边正文那一件各写一份（同一件里的东西不该由别人替它画），
   4px 的间距两边必须一样。 */
.qdet__label {
  margin-bottom: 4px;
}

.qdet__ladder {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 梯子那一格 28px 高：和 `AdminNumberList` 的行同高，两块竖排的列表挨在一起时
   行距不会一紧一松。 */
.qdet__rung {
  display: block;
  width: 100%;
  height: 28px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qdet__rung:hover {
  color: var(--text);
  background: var(--fill);
}

/* 当前那一格：`--fill-2` 底 + `--ink` 字。**不是琥珀** —— 详情这一屏的琥珀留给
   操作栏那颗主按钮（§7.4 的详情 = 1）。 */
.qdet__rung--on,
.qdet__rung--on:hover {
  color: var(--ink);
  font-weight: 600;
  background: var(--fill-2);
}

.qdet__facts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.qdet__fact {
  display: flex;
  align-items: center;
  gap: 12px;
}

.qdet__fact > .t-meta-read {
  flex: 0 0 64px;
}

.qdet__fact-value {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.qdet__timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.qdet__tl-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}

.qdet__tl-word {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
