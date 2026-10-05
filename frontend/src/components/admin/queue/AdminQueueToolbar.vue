<script setup lang="ts">
import type { QueueWindowKey } from '@/composables/useAdminQueue'
import type { FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminTabs from '@/components/admin/AdminTabs.vue'

// 队列页的工具行（48px）：栏位 + 搜索 + 日期窗口 chip + 状态页签。**四组东西说着两件事**，
// 位置分组和文案都在重复这一点：栏位 / 搜索 / 窗口 chip 是**服务端的问法**（换了重拉），
// 状态页签是**在手上这一页里筛**（服务端没有按状态查的参数）—— 页签右侧那句「只筛这一页」
// 把这个口径写在面上。
//
// 所以栏位那一组收在一段底色里的分段控件里，和状态页签那排裸药丸刻意长得不一样：四个栏位
// **都画出来**而不是收进一个下拉，因为这一页最贵的一类错就是「管理员以为某条反馈不见了，
// 其实它在隔壁那一栏」。
//
// 这一件只吃 props、只往上发事件：换栏位、改关键词、清窗口、换状态页签各自报回去。它不
// 认识 store、不认识接口、不认识路由。搜索框**住在这里**，所以页面那一层要的「把焦点送进
// 搜索框」（`/` 键和清窗口之后）由 `defineExpose` 出去 —— 位置是这一件的事，什么时候要
// 焦点是页面的事。
defineOptions({ name: 'AdminQueueToolbar' })

defineProps<{
  /** 当前栏位。 */
  lane: AdminTab
  /** 四个栏位各自的名字。**由页面给**（它要 `t`，而键名得逐字写在源码里，见 useAdminQueue）。 */
  lanes: { value: AdminTab; label: string }[]
  /** 输入框里正在打的字。 */
  query: string
  /** 生效中的日期窗口，一颗 chip 一段。空数组 = 那一行整个不画。 */
  chips: { key: QueueWindowKey; text: string; clearAria: string }[]
  /** 当前状态页签（`all` = 不过滤）。 */
  status: FeedbackStatus | 'all'
  /** 状态页签的档位与文案。 */
  statusOptions: { value: FeedbackStatus | 'all'; label: string }[]
  /** 状态页签右侧那句口径注画不画（只在筛选真的生效时出现）。 */
  showScopeNote: boolean
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  'update:status': [value: FeedbackStatus | 'all']
  'select-lane': [lane: AdminTab]
  'clear-window': [key: QueueWindowKey]
}>()

const { t } = useI18n()

const searchEl = ref<HTMLInputElement | null>(null)

/** 把焦点送进搜索框。`/` 键、以及 chip 被清掉之后（`clearWindow`）都走这一条。 */
defineExpose({ focusSearch: () => searchEl.value?.focus() })
</script>

<template>
  <div class="qpage__tools">
    <!-- 栏位（公开 / 私密 / AI 队友提的 / 安全）。**收在一段底色里的分段控件**，
         和右边那排裸药丸刻意长得不一样 —— 两者不是一回事：这一排换的是「去要哪一批
         数据」（服务端的 `tab`），右边那排是在**已经拿到的那一页里**筛。四个都画出来
         而不是收进一个下拉：当前停在哪一栏必须一眼看得见。 -->
    <div class="qpage__lanes" role="radiogroup" :aria-label="t('feedback.queue.lane.label')">
      <span class="qpage__lanes-label">{{ t('feedback.queue.lane.label') }}</span>
      <button
        v-for="item in lanes"
        :key="item.value"
        type="button"
        class="qpage__lane"
        :class="{ 'qpage__lane--on': lane === item.value }"
        role="radio"
        :aria-checked="lane === item.value"
        @click="emit('select-lane', item.value)"
      >
        {{ item.label }}
      </button>
    </div>

    <div class="qpage__search">
      <v-icon icon="mdi-magnify" size="16" class="qpage__search-icon" aria-hidden="true" />
      <input
        ref="searchEl"
        :value="query"
        type="text"
        class="qpage__search-input"
        :placeholder="t('feedback.queue.search.placeholder')"
        :aria-label="t('feedback.queue.search.placeholder')"
        autocomplete="off"
        spellcheck="false"
        @input="emit('update:query', ($event.target as HTMLInputElement).value)"
      />
    </div>

    <!-- 日期窗口 chips（看板 KPI 深链带进来的那几段）。放在搜索框后、状态页签前：
         窗口是**服务端的问法**，和栏位 / 搜索归一组；状态页签是客户端筛。
         四态下照画 —— 它们是控件不是数据，错误态带着窗口重试是正当需求。 -->
    <div v-if="chips.length" class="qpage__windows" role="group" :aria-label="t('feedback.queue.window.label')">
      <span v-for="chip in chips" :key="chip.key" class="qpage__wchip">
        {{ chip.text }}
        <button
          type="button"
          class="qpage__wchip-x"
          :aria-label="chip.clearAria"
          @click="emit('clear-window', chip.key)"
        >
          <v-icon icon="mdi-close" size="12" aria-hidden="true" />
        </button>
      </span>
    </div>

    <!-- 状态页签。和看板的分类、看板的窗口、模型页的分档共用 `AdminTabs`
         （下划线式，放不下时整排横着滚、右缘渐隐）：这三处以前各写一份，
         切分区时同一件事的手感不一样。 -->
    <AdminTabs
      class="qpage__tabs"
      :model-value="status"
      :options="statusOptions"
      :label="t('feedback.queue.label')"
      @update:model-value="emit('update:status', $event)"
    />

    <!-- 状态页签的口径注：只在筛选真的生效时出现。面上留「只筛这一页」五个字
         （口径是正确性问题，不能整句藏进 title），完整句进 title。不进
         radiogroup —— 它不是选项，不污染组的语义。 -->
    <span v-if="showScopeNote" class="qpage__tabs-note" :title="t('feedback.queue.tab.scopeHint')">
      {{ t('feedback.queue.tab.scope') }}
    </span>
  </div>
</template>

<style scoped>
/* 工具行。**让它能折行**（`min-height` 而不是 `height`）：宽屏上三组东西并排正好
   48px（32 的内容 + 上下 8 的 padding），窄到装不下时折成两行而不是把状态页签挤出
   容器 —— 加栏位那一组之前，这一行只有两组，装得下是巧合，不是余量。

   白底：它和上面的页头是**同一条白色带**（后台三层骨架的头一层），底下才是灰画布。
   那条 `--line` 就是这条白带唯一的下边线。 */
.qpage__tools {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  min-height: 48px;
  padding: 8px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

/* 栏位那一组：收在一段底色里的分段控件，和页头那个视图切换同一个形状。**和右边那排
   状态页签刻意长得不一样**，因为两者不是一回事（见文件开头）。 */
.qpage__lanes {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 4px 0 10px;
  background: var(--fill);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
}

/* 「栏位」两个字只做说明：它说的是右边那四颗是什么，自己不参与点选。 */
.qpage__lanes-label {
  margin-right: 4px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.qpage__lane {
  height: 24px;
  padding: 0 10px;
  background: transparent;
  border: 1px solid transparent;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__lane:hover {
  color: var(--text);
}

/* 选中的那一颗：抬到底色之上（`--surface` + 一圈描边）。中性色 —— 栏位是一个位置，
   不是一条告警，琥珀只留给当前选项卡和主操作。 */
.qpage__lane--on,
.qpage__lane--on:hover {
  background: var(--surface);
  border-color: var(--line);
  color: var(--ink);
}

.qpage__search {
  display: flex;
  flex: 0 0 260px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
}

/* 焦点环跟着全局那一套走（`--focus-ring`，2px）。这里只换边框色：搜索框的框本身就是
   焦点指示的载体，再套一圈 outline 会在 32px 高的小方块外多出一层。 */
.qpage__search:focus-within {
  border-color: var(--focus-ring);
}

.qpage__search-icon {
  flex: 0 0 auto;
  color: var(--muted);
}

.qpage__search-input {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  outline: none;
}

.qpage__search-input::placeholder {
  color: var(--muted);
}

/* 日期窗口 chips。中性色不染色：窗口是筛选条件不是告警。工具行本就 `flex-wrap`，
   三颗 chip 满编时折行不挤翻页签；chips 自己也能折（窄屏下它们跟着工具行换行，
   一颗也不会被裁）。 */
.qpage__windows {
  display: flex;
  flex: 0 1 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.qpage__wchip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 24px;
  padding: 0 4px 0 10px;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

/* chip 上的 ×：普通 button，天然进 Tab 序；字母键在 window 级、`isTyping` 不误伤
   （与翻页按钮现状一致）。 */
.qpage__wchip-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qpage__wchip-x:hover {
  background: var(--fill-2);
  color: var(--text);
}

/* 状态页签那一排是 `AdminTabs`（下划线式）。这里只管它在工具行里怎么占位：它排在
   最后，能缩、能横着滚，不把前面的栏位和搜索框挤出去。 */
.qpage__tabs {
  flex: 0 1 auto;
  min-width: 0;
  margin-left: auto;
}

/* 状态页签的口径注：12px --muted，出现在页签右侧，不推走任何已有控件。 */
.qpage__tabs-note {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

/* 内容列窄于 700（容器查询挂在后台内容列上，§3.5，不是视口）：工具行不再挤在一行上。
   - 栏位那一组整条**横着滑**而不是折行：四颗药丸连标签 300px 出头，390 下差一点点，
     折行会把「安全」单独甩到第二行、看起来像另一组控件；滑动保持它是一组。
   - 搜索框独占一行（260px 的定宽在 390 下会把行撑破）。
   - 状态页签那一排 `AdminTabs` 自己会横着滚，这里只把它从右对齐改回左对齐。 */
@container admin (max-width: 700px) {
  .qpage__tools {
    gap: 12px;
  }

  .qpage__lanes {
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .qpage__search {
    flex: 1 1 100%;
  }

  .qpage__tabs {
    margin-left: 0;
  }
}
</style>
