<script setup lang="ts">
// 反馈那几页共用的**版面壳**：滚动容器 + 内容列宽 + 页头。
//
// 抽它的理由不是「少写几行」：`.fb-page` / `.fb-page__inner` / `.fb-head` 这三条规则
// 原先在四个文件里**逐字重复**（中心、我的、提交、详情），而它们一起管着两件不能各写
// 各的事：
//
//   1. **谁来滚**。common.scss 把 html/body/#app 定成固定高度 + `overflow: hidden`，
//      滚动由每一页自己领。四个文件里少写一次，那一页的内容就有一截永远够不着，而且
//      屏幕上没有任何东西看起来坏了（见 views/feedback/scroll.spec.ts）。写在这里
//      只有一处会错。
//   2. **页边距和内容宽度**（24/16 + `--page-w`）。四份拷贝意味着「窄屏页边距」有四个
//      答案，改一处的那三次看起来像漏改。
//
// 页头只有一种形状：标题在左、动作在右、副标题在下一行。详情页那种「返回链接 + 标题 +
// 状态药丸 + 作者」的富页头用 `#head` 整个替换掉默认那一段 —— 与其把四种页头塞进一堆
// 布尔参数，不如让那一页自己画，壳只负责它外面那一层。
//
// 槽位：
//   `#lead`    标题**左边**的东西（提交页的返回箭头）。
//   `#actions` 标题右边的动作（按钮）。
//   `#head`    整段页头（用上它之后 lead/title/actions/sub 都不画）。
//   `#sub`     标题下面那一行说明。
//   default    正文。
defineOptions({ name: 'FeedbackPageShell' })

withDefaults(
  defineProps<{
    /** 页标题。空着就不画页头那一行（详情的加载/空态不需要标题）。 */
    title?: string
    /** 多列内容（详情页的两栏）用宽档，其余一律阅读宽度。 */
    wide?: boolean
    /** 底部留白交给页内的黏底元素（详情页的评论框和操作栏），壳不再自己加。 */
    flushBottom?: boolean
  }>(),
  { title: '', wide: false, flushBottom: false }
)
</script>

<template>
  <!-- `fill-height overflow-y-auto` 不是装饰，是这一页能不能滚的全部（见文件头第 1 条）。 -->
  <div class="fb-page fill-height overflow-y-auto" :class="{ 'fb-page--flush': flushBottom }">
    <div class="fb-page__inner" :class="wide ? 'page-container--wide' : 'page-container'">
      <slot v-if="$slots.head" name="head" />

      <template v-else-if="title">
        <header class="fb-head">
          <slot name="lead" />
          <h1 class="t-page-title fb-head__title">{{ title }}</h1>
          <v-spacer v-if="$slots.actions" />
          <div v-if="$slots.actions" class="fb-head__actions">
            <slot name="actions" />
          </div>
        </header>
        <slot name="sub" />
      </template>

      <slot />
    </div>
  </div>
</template>

<style scoped>
.fb-page {
  /* 左右 16 是窄屏的页边距：容器本身居中且有 max-width，宽屏上真正撑开版面的是
     page-container，不是这 16px。 */
  padding: 24px 16px 48px;
}
/* 详情页最后两样东西都黏在底边上（评论框、操作栏），而 `sticky` 量的是滚动容器**内容
   盒**的下沿 —— 多出来的底内边距会变成它们下面的一条带子，评论从那里往上滚、从框底下
   露出来。所以那一页把底边留白交给页内元素，壳不加。 */
.fb-page--flush {
  padding-bottom: 0;
}
.fb-page__inner {
  margin: 0 auto;
}
.fb-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.fb-head__title {
  min-width: 0;
  margin: 0;
}
.fb-head__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
