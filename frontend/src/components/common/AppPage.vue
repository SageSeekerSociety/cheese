<script setup lang="ts">
// 一页的外形：一条页头 + 下面唯一会滚的正文。项目里的页面、「我的设备」这样的个人
// 页面都用它，页名和按钮那一行全站只在这里画。
//
// 页头和房间的话题头是同一条线：高度、底线都读 --app-page-header-*，和左边侧栏
// 顶上那条项目名对齐。以前这几页各自画一个大标题（有的写项目名、有的写页名、
// 有的把两者倒过来），侧栏那条底线到了这几页就断在半空，从房间切过来整条线一
// 会儿有一会儿没有。页头写的是「这一页是什么」；项目名在侧栏上，不在这里再写一遍。
//
// 页头右边的按钮不由页面写在模板里：页面用 useCommands 登记标了 `header` 的命令，
// 这里把它们画成按钮。手机上这一行不画，同一批命令由顶栏画（MobileAppBar）。
//
// 宽度也归这里：`read` 是读和填表的那一栏（--page-w），`full` 给多列的工作面（看
// 板），`admin` 是管理后台的工作台（--page-w-admin）。页面不再各自写一个数字。
// `read` 和 `admin` 的页头标题和正文列从同一条左沿开始。
import type { NavTarget } from '@/lib/navTarget'

import { useDisplay } from 'vuetify'

import { headerCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'

withDefaults(
  defineProps<{
    title: string
    width?: 'read' | 'full' | 'admin'
    // 这一页是另一页里的一项（成员名册里的一个人）：页头写成「成员 / 名字」，前
    // 一段点回去。
    parent?: { label: string; to: NavTarget }
    // 正文自己会滚（一整篇文档、带自己的工具条和评论栏）：这一层不滚，把整个高度让给它。
    fill?: boolean
  }>(),
  { width: 'read', parent: undefined, fill: false }
)

defineSlots<{
  default?: () => unknown
  // 标题右边紧跟着的一段短状态（「已保存」「施工中 2 · 已完成 1」）。
  meta?: () => unknown
  // 页头右边、命令按钮前面的控件，不是一件「做」的事（看板的「只看我的」开关）。
  // 它只在桌面、以及手机上有 #meta 撑起这一行时才看得见。
  controls?: () => unknown
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <div class="app-page">
    <!-- 手机上页名写在顶栏里（路由的 title），按钮也由顶栏画，这一条只剩状态；没有
         状态就整条不画。 -->
    <header v-if="mdAndUp || $slots.meta" class="app-page__head" :class="`app-page__head--${width}`">
      <div class="app-page__head-row">
        <h1 v-if="mdAndUp" class="app-page__title t-title">
          <template v-if="parent">
            <NavLink :to="parent.to" class="app-page__parent">{{ parent.label }}</NavLink>
            <span class="app-page__sep" aria-hidden="true">/</span>
          </template>
          {{ title }}
        </h1>
        <div v-if="$slots.meta" class="app-page__meta"><slot name="meta" /></div>
        <!-- 页头上的按钮是小号的文字按钮，图标在字前面；标了 accent 的那一颗是这一页
           的主操作，琥珀色实心。 -->
        <div v-if="$slots.controls || (mdAndUp && headerCommands.length)" class="app-page__actions">
          <slot name="controls" />
          <template v-for="command in headerCommands" :key="command.id">
            <BaseButton
              v-if="command.header?.iconOnly"
              kind="ghost"
              :icon="command.icon"
              size="sm"
              :to="command.to"
              :loading="command.loading"
              :disabled="command.disabled"
              :aria-label="command.title"
              :title="command.title"
              @click="command.run?.()"
            />
            <BaseButton
              v-else
              :kind="command.header?.accent ? 'primary' : 'ghost'"
              :prepend-icon="command.icon"
              size="sm"
              :to="command.to"
              :loading="command.loading"
              :disabled="command.disabled"
              @click="command.run?.()"
            >
              {{ command.title }}
            </BaseButton>
          </template>
        </div>
      </div>
    </header>
    <div class="app-page__body" :class="[`app-page__body--${width}`, { 'app-page__body--fill': fill }]">
      <div class="app-page__column" :class="[`app-page__column--${width}`, { 'app-page__column--fill': fill }]">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
.app-page__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 12px;
  height: var(--app-page-header-height);
  padding: 0 16px;
  border-bottom: var(--app-page-header-rule);
}
/* 一般情况下这一层不占盒子，页头照旧是一条 flex 行。 */
.app-page__head-row {
  display: contents;
}
/* 读的那一档（--page-w）和后台那一档（--page-w-admin）的内容列封顶、居中。页头那一行
   跟着它一起封顶居中，标题和正文在任何宽度下都从同一条竖线开始。正文滚动时右边有滚动
   条，页头和正文都留出同样宽的滚动条槽位（`scrollbar-gutter`），两边居中的基准才是同
   一个宽度。满宽那一档见下面。 */
.app-page__head--read,
.app-page__body--read,
.app-page__head--admin,
.app-page__body--admin {
  scrollbar-gutter: stable;
}
.app-page__head--read,
.app-page__head--admin {
  display: block;
  overflow: hidden;
  padding: 0;
}
.app-page__head--read .app-page__head-row,
.app-page__head--admin .app-page__head-row {
  display: flex;
  align-items: center;
  gap: 12px;
  box-sizing: border-box;
  height: 100%;
  margin-inline: auto;
  padding: 0 16px;
}
.app-page__head--read .app-page__head-row {
  max-width: calc(var(--page-w) + 32px);
}
.app-page__head--admin .app-page__head-row {
  max-width: var(--page-w-admin);
}
/* 满宽那一档的正文自己管内边距，铺满内容区的几页（看板、资料库、团队的项目、知识库、
   工作电脑）都离左边 24。页头标题跟着它们缩进同样的距离。 */
.app-page__head--full {
  padding: 0 24px;
}
/* 手机上这一行只剩状态，控件和状态挤不下时叠成两行：看板的「只看我的」和那一行
   统计原来并排，窄屏上开关直接压在数字上。满宽那一档在手机上让这一行换行，控件
   自己占一行。 */
@media (max-width: 959.98px) {
  .app-page__head--full {
    height: auto;
    min-height: var(--app-page-header-height);
    align-content: center;
    flex-wrap: wrap;
    row-gap: 4px;
  }

  .app-page__head--full .app-page__actions {
    flex-basis: 100%;
    margin-inline-start: 0;
  }
}
.app-page__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-page__parent {
  color: var(--muted);
  font-weight: 400;
  text-decoration: none;
  transition: color var(--dur-quick) var(--ease-standard);
}
.app-page__parent:hover {
  color: var(--ink);
}
.app-page__sep {
  margin-inline: 8px;
  color: var(--faint);
  font-weight: 400;
}
.app-page__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
/* 窄屏上按钮一多就横着滚，不换行：这一条的高度是钉死的，换行会把第二行按钮挤出
   页头。 */
.app-page__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  margin-inline-start: auto;
  overflow-x: auto;
  scrollbar-width: none;
}
.app-page__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.app-page__column {
  margin-inline: auto;
  padding: 24px 16px 48px;
}
.app-page__body--fill {
  display: flex;
  overflow: hidden;
}
.app-page__column--fill {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding-bottom: 0;
}
.app-page__column--read {
  max-width: calc(var(--page-w) + 32px);
}
/* 后台那一档也自己管内边距（表格、卡片各有各的内缩）。断点都是容器查询，所以这一列
   是查询容器；`container-type` 做了行内尺寸包含，宽度推不出来，必须写 `width: 100%`。
   列至少和正文一样高：队列那张表要撑到底。 */
.app-page__column--admin {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 100%;
  max-width: var(--page-w-admin);
  min-height: 100%;
  padding: 0;
  container-type: inline-size;
}
/* 满宽的那种自己管内边距：看板那几列各自滚动，得把高度一路钉到底。 */
.app-page__column--full {
  height: 100%;
  padding: 0;
}
</style>
