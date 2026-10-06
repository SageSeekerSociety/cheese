<script setup lang="ts">
import type { FeedbackCard, FeedbackKind, FeedbackStatus } from '@/cx_types'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import FeedbackCardView from '@/components/feedback/FeedbackCardView.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import { t } from '@/i18n'

// 反馈中心首页 (/feedback) 的**画面那一半**：只吃 props、只往上发事件。
//
// 取数（词表、第一页、翻页）、跳转、筛选那一格本地镜像、以及「列表为什么是空的」都在
// `FeedbackCenterPage.vue` 那一半；页头那副壳（`FeedbackPageShell` + 标题 + 动作）也
// 留在那里，因为 `scroll.spec.ts` 钉着这一页的根节点就是那个壳（理由：滚动归壳领）。
// 这一半画的是壳里面的正文 —— 栏位页签、筛选条、热门那一栏的注脚、列表。分开的形状见
// docs 里「容器 + 视图」那套：这一半要能被单独渲染（场景棘轮要求它自己就是 A 级），
// 所以它不能读 store、读路由，也不能渲染任何取数的子组件 —— 支持按钮按下时发
// `support`，由外面上报的那一半去调 store。
//
// ## 版面
//
// 正文这几层**由共用件拼出来**，不再各画一遍：`AdminTabs` 是管理控制台同一排页签
// （下划线 + 计数），`FeedbackList` + `AdminEmptyState` 是「一张面 + 头发丝分隔的行」
// 那副骨架，四种「没有」共用它。中心页和「我的反馈」两页因此只有一份列表实现。
//
// ## 筛选条为什么是这一排药丸
//
// 上一版是四个 `v-select` / `v-text-field` 横排：outlined 的输入框在 1440 下四个各自
// 撑满一格、字比列表还大，而它们要回答的三个问题（谁提的、哪一类、什么时候）各自
// 只有三五个取值 —— 值得一次点开下拉的只有「作者」那一栏，而它是打字。所以现在：
//
//   * 取值离散的三栏（类型 / 状态 / 时间）就地排成**一排药丸**，选中哪一颗一眼看得见，
//     一次点击就换（下拉要点两下）。默认那颗是「不限」，也就是「这一栏没加条件」。
//   * 「作者」留一个**手画的输入框**（和搜索框同一副骨架），带一颗清除的叉。
//
// 配色和尺寸走令牌，和管理控制台的工具栏是同一套（`--fill` 的药丸、`--surface` +
// `--line` 的输入框、`--focus-ring` 的焦点环）。筛选在服务端 —— 这里只发事件，由外面
// 那一半去调 store；默认都不生效（全部「不限」），所以这一页第一眼的样子没变：多出来的
// 是一排控件，不是一层常态的过滤。
type EmptyState = {
  icon: string
  title: string
  desc: string
  tone?: 'neutral' | 'error'
  /** 空态里那颗按钮的文案。动作由外面那一半按「为什么会空」决定（重试 / 清除筛选），
   *  这一半只管把字画出来。 */
  action?: string
}

const props = defineProps<{
  /** 搜索词。**store 才是那一份**，所以输入框是受控的，值从这里来。 */
  query: string
  tab: string
  /** 栏位：服务端给的取值 + 词表里的名字 + 各栏的计数。 */
  tabs: { value: string; label: string; count: number }[]
  /** 三栏离散筛选的取值。**「不限」不是一个值，是「这一栏没加条件」** —— 它的 value
   *  是 null，服务端收到 null 就是不按这一栏筛。 */
  kindOptions: { value: FeedbackKind | null; title: string }[]
  statusOptions: { value: FeedbackStatus | null; title: string }[]
  dayOptions: { value: number | null; title: string }[]
  /** 筛选条上那几格控件当前选中的值（store 的本地镜像）。 */
  kind: FeedbackKind | null
  status: FeedbackStatus | null
  days: number | null
  author: string
  /** 栏位或那四个筛选收窄了 —— 决定「清除筛选」那颗按钮在不在。 */
  hasFilters: boolean
  /** 「热门」那一栏的规则说明；不在那一栏时是 null。 */
  hotNote: string | null
  loading: boolean
  items: FeedbackCard[]
  hasMore: boolean
  loadingMore: boolean
  /** 服务端一共几条（不是手上几条）。 */
  total: number
  error: string | null
  /** 空列表那副骨架的文案与动作，由容器按「为什么会空」算好。 */
  empty: EmptyState
}>()

const emit = defineEmits<{
  (e: 'search', value: string): void
  (e: 'set-tab', value: string): void
  (e: 'set-kind', value: FeedbackKind | null): void
  (e: 'set-status', value: FeedbackStatus | null): void
  (e: 'set-days', value: number | null): void
  (e: 'update:author', value: string): void
  (e: 'clear-filters'): void
  (e: 'empty-action'): void
  (e: 'dismiss'): void
  (e: 'more'): void
  (e: 'support', id: string): void
}>()

/** 搜索框和作者框都是手画的 `<input>`：把发生的原生事件收成值再往上发。 */
function onSearchInput(event: Event) {
  emit('search', (event.target as HTMLInputElement).value)
}
function onAuthorInput(event: Event) {
  emit('update:author', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <!-- 栏位切换走一个事件，不直接绑 store.tab：栏位是**服务端**的筛选，改了的下一件事
       必然是重新拉一页，绑赋值就把那一步留在外面那一半了。 -->
  <AdminTabs
    :model-value="props.tab"
    :options="props.tabs"
    :label="t('feedback.center.tab.label')"
    class="fb-tabs"
    @update:model-value="emit('set-tab', $event)"
  />

  <!-- 筛选条。默认全部「不限」，所以这一页第一眼的样子没变；那一排药丸说的是
       「这一栏里哪些」，栏位页签说的是「哪一栏」。 -->
  <div class="fb-tools">
    <div class="fb-search">
      <v-icon icon="mdi-magnify" size="16" class="fb-search__icon" aria-hidden="true" />
      <input
        :value="props.query"
        type="text"
        class="fb-search__input"
        :placeholder="t('feedback.center.search.placeholder')"
        :aria-label="t('feedback.center.search.placeholder')"
        autocomplete="off"
        spellcheck="false"
        @input="onSearchInput"
      />
    </div>

    <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.kind')">
      <span class="fb-fgroup__label">{{ t('feedback.center.filter.kind') }}</span>
      <button
        v-for="option in props.kindOptions"
        :key="String(option.value)"
        type="button"
        class="fb-pill"
        :class="{ 'fb-pill--on': props.kind === option.value }"
        role="radio"
        :aria-checked="props.kind === option.value"
        @click="emit('set-kind', option.value)"
      >
        {{ option.title }}
      </button>
    </div>

    <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.status')">
      <span class="fb-fgroup__label">{{ t('feedback.center.filter.status') }}</span>
      <button
        v-for="option in props.statusOptions"
        :key="String(option.value)"
        type="button"
        class="fb-pill"
        :class="{ 'fb-pill--on': props.status === option.value }"
        role="radio"
        :aria-checked="props.status === option.value"
        @click="emit('set-status', option.value)"
      >
        {{ option.title }}
      </button>
    </div>

    <div class="fb-fgroup" role="radiogroup" :aria-label="t('feedback.center.filter.days')">
      <span class="fb-fgroup__label">{{ t('feedback.center.filter.days') }}</span>
      <button
        v-for="option in props.dayOptions"
        :key="String(option.value)"
        type="button"
        class="fb-pill"
        :class="{ 'fb-pill--on': props.days === option.value }"
        role="radio"
        :aria-checked="props.days === option.value"
        @click="emit('set-days', option.value)"
      >
        {{ option.title }}
      </button>
    </div>

    <!-- 「作者」是这一排里唯一要打字的：它的取值不是三五个，是一个 handle。
         清除的叉**只在框里有字时出现** —— 常驻的话它是一颗平时没有任何作用的按钮。 -->
    <div class="fb-fgroup">
      <label class="fb-fgroup__label" for="fb-author">{{ t('feedback.center.filter.author') }}</label>
      <div class="fb-author">
        <input
          id="fb-author"
          :value="props.author"
          type="text"
          class="fb-author__input"
          :placeholder="t('feedback.center.filter.authorPlaceholder')"
          autocomplete="off"
          spellcheck="false"
          @input="onAuthorInput"
        />
        <button
          v-if="props.author"
          type="button"
          class="fb-author__x"
          :aria-label="t('feedback.center.filter.clear')"
          @click="emit('update:author', '')"
        >
          <v-icon size="12" aria-hidden="true">mdi-close</v-icon>
        </button>
      </div>
    </div>

    <!-- 「清除筛选」只在真有筛选时出现：常驻的话它是一颗平时没有任何作用的按钮，
         而这一排里已经有五组控件了。 -->
    <button v-if="props.hasFilters" type="button" class="fb-clear" @click="emit('clear-filters')">
      {{ t('feedback.center.filter.clear') }}
    </button>
  </div>

  <!-- 「热门」凭什么这么排，是这一页唯一一处读者猜不出来的规则：它看着像按支持数
       排，其实是按「支持数按半衰期折过的分数」排 —— 一条二十个支持的老反馈排在一条
       今天刚爆的上面，不解释一句就只是「这个排序坏了」。
       三个数都由服务端随 `meta` 发下来，外面那一半只负责把它们说成人话：前端自己再算
       一遍的话（哪怕只是把 2 写死在这句话里），改阈值就要改两处，而两处漂开的表现是
       「说明和实际排序对不上」，页面上看不出任何异常。
       加载中不藏它：这句话说的是这一栏的规则，不是这一栏的结果，跟着骨架一起闪一下
       反倒是多一次闪动。 -->
  <p v-if="props.hotNote" class="t-meta-read t-num fb-hot-note">
    {{ props.hotNote }}
  </p>

  <!-- 列表**非空**时的失败也要画出来。以前 error 只在下面那块「一条也没有」里
       渲染，于是从卡片上点「支持」失败（已办完的条目回 412）时页面上什么都不动：
       按钮按得下去、数字不变、一句话也没有 —— 和「这个按钮坏了」长得一模一样。
       列表为空时下面那块画同一句话（并且带重试），这里不重复画。 -->
  <FeedbackErrorBanner v-if="props.error && props.items.length" :message="props.error" @dismiss="emit('dismiss')" />

  <!-- 列表：一张面 + 头发丝分隔的行（`FeedbackList`）。骨架行数由它自己定（§9.4），
       这一页的「一行」是一条 60 来像素的反馈行，不是 132px 的卡。 -->
  <FeedbackList
    :loading="props.loading"
    :count="props.items.length"
    :has-more="props.hasMore"
    :loading-more="props.loadingMore"
    :shown="props.items.length"
    :total="props.total"
    @more="emit('more')"
  >
    <!-- 打开详情那条链接在行自己身上（`router-link`），这里不再接一个 `@open`
         去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
    <FeedbackCardView v-for="item in props.items" :key="item.id" :item="item" @support="emit('support', $event)" />

    <template #empty>
      <AdminEmptyState
        :title="props.empty.title"
        :desc="props.empty.desc"
        :icon="props.empty.icon"
        :tone="props.empty.tone"
        :action="props.empty.action"
        @action="emit('empty-action')"
      >
        <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，
             这一句说的是「这一次为什么没成」—— 两句不是一回事，少一句就只剩
             「检查网络后重试」，而失败可能压根不是网络（比如没有权限）。 -->
        <p v-if="props.error" class="fb-empty__raw t-meta-read">{{ props.error }}</p>
      </AdminEmptyState>
    </template>

    <!-- 页脚说的是**在这个页面上提交**会发生什么，而这条路提出来的反馈没有房间来源
         （`topic_id` 只有 accept 端点解得出），所以不提房间那一档——详情页和卡片上的
         那句说的是一条已经存在的行，它可能有房间，两处不一样是对的。 -->
    <template #foot>{{ t('feedback.center.foot') }}</template>
  </FeedbackList>
</template>

<style scoped>
/* 栏位页签下面那条底线由 `AdminTabs` 自己画（选中项的 2px 琥珀下划线），这里只给它
   和下面一排控件之间的距离。 */
.fb-tabs {
  margin-bottom: 4px;
}
/* 筛选条：一组一组排，放不下就整组折行。组**自己不折行**（一颗药丸不该和它的同伴
   分开），所以窄屏上是一组一行。 */
.fb-tools {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 16px;
  margin-bottom: 16px;
}
/* 搜索框：和管理控制台那把是同一副骨架（32px、`--surface` + `--line`、焦点换边框色
   而不是再套一圈 outline）。宽度收在 240 —— 再宽它也只是这一排里的一格。 */
.fb-search {
  display: flex;
  flex: 0 0 240px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-search:focus-within {
  border-color: var(--focus-ring);
}
.fb-search__icon {
  color: var(--faint);
}
.fb-search__input {
  width: 100%;
  min-width: 0;
  height: 100%;
  padding: 0;
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  background: transparent;
  border: 0;
  outline: none;
}
.fb-search__input::placeholder {
  color: var(--faint);
}
/* 一组筛选：一个 12px 的栏名 + 一排药丸。栏名是**说明**（它说的是右边那几颗是什么），
   自己不参与点选，所以用 --muted 而不是 --ink。 */
.fb-fgroup {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
}
.fb-fgroup__label {
  margin-right: 2px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
/* 药丸：`--fill` 底、选中那颗抬到 `--surface` + 一圈描边（和管理端的「栏位」同一个
   控件、同一套颜色）。高 24 —— 它是这一排里最小的可点目标，再小就不好按了。 */
.fb-pill {
  height: 24px;
  padding: 0 10px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
  background: var(--fill);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.fb-pill:hover {
  color: var(--text);
}
.fb-pill--on {
  color: var(--ink);
  background: var(--surface);
  border-color: var(--line-2);
}
/* 作者那一栏：一个 32px 的输入框，和搜索框同高同框（两者都是打字）。 */
.fb-author {
  display: flex;
  align-items: center;
  gap: 4px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 6px 0 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-author:focus-within {
  border-color: var(--focus-ring);
}
.fb-author__input {
  flex: 0 1 120px;
  min-width: 0;
  height: 100%;
  padding: 0;
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  background: transparent;
  border: 0;
  outline: none;
}
.fb-author__input::placeholder {
  color: var(--faint);
}
.fb-author__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  color: var(--faint);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.fb-author__x:hover {
  color: var(--ink);
  background: var(--fill);
}
/* 「清除筛选」是一条文字按钮，不是药丸：它清的是**这一排全部**，比任何一颗药丸都大
   一档，画成同样的形状会让人以为它只清旁边那一组。 */
.fb-clear {
  height: 24px;
  padding: 0 8px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.fb-clear:hover {
  color: var(--ink);
  background: var(--fill);
}
/* 热门那一栏的规则说明：页签之后 12px，往下和列表也是 12px。
   不给底色、不加图标 —— 它是这一栏的注脚，不是警告，抬成一块提示框会让人以为
   热门这一栏出了什么问题。 */
.fb-hot-note {
  margin: 0 0 12px;
  line-height: var(--lh-14-loose);
}
/* 空态里那句服务端的原话。空态那副骨架（`AdminEmptyState`）把动作放在这一句上面，
   所以这里只补一点上边距。 */
.fb-empty__raw {
  margin: 8px 0 0;
}
/* 窄屏：搜索框占满一整行（它在这一排里是最常用的那一格），其余每组各占一行。
   药丸组自己在窄屏上折行 —— 英文的栏名和取值（"In progress" 那一档）比中文长一倍，
   不折的话 390 上会横向溢出去。 */
@media (max-width: 599.98px) {
  .fb-search {
    flex: 1 1 100%;
  }
  .fb-fgroup {
    flex: 1 1 100%;
  }
  .fb-author__input {
    flex: 1 1 auto;
  }
}
</style>
