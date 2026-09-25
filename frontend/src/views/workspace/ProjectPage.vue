<script setup lang="ts">
// 项目框架里一页的外形：一条页头 + 下面唯一会滚的正文。
//
// 页头和房间的话题头是同一条线：高度、底线都读 --app-page-header-*，和左边侧栏
// 顶上那条项目名对齐。以前这几页各自画一个大标题（有的写项目名、有的写页名、
// 有的把两者倒过来），侧栏那条底线到了这几页就断在半空，从房间切过来整条线一
// 会儿有一会儿没有。页头写的是「这一页是什么」；项目名在侧栏上，不在这里再写一遍。
//
// 宽度也归这里：`read` 是读和填表的那一栏（--page-w），`full` 给多列的工作面（看
// 板）。页面不再各自写一个数字。
import type { RouteLocationRaw } from 'vue-router'

import { useDisplay } from 'vuetify'

withDefaults(
  defineProps<{
    title: string
    width?: 'read' | 'full'
    // 这一页是另一页里的一项（成员名册里的一个人）：页头写成「成员 / 名字」，前
    // 一段点回去。
    parent?: { label: string; to: RouteLocationRaw }
  }>(),
  { width: 'read', parent: undefined }
)

defineSlots<{
  default?: () => unknown
  // 标题右边紧跟着的一段短状态（「已保存」「施工中 2 · 已完成 1」）。
  meta?: () => unknown
  // 这一页的操作，靠右。
  actions?: () => unknown
}>()

const { mdAndUp } = useDisplay()

const ACTION_DEFAULTS = { VBtn: { variant: 'text', size: 'small' } } as const
</script>

<template>
  <div class="project-page">
    <!-- 手机上页名写在顶栏里（路由的 title），这一条只剩状态和操作；两样都没有就
         整条不画。 -->
    <header v-if="mdAndUp || $slots.meta || $slots.actions" class="project-page__head">
      <h1 v-if="mdAndUp" class="project-page__title t-title">
        <template v-if="parent">
          <router-link :to="parent.to" class="project-page__parent">{{ parent.label }}</router-link>
          <span class="project-page__sep" aria-hidden="true">/</span>
        </template>
        {{ title }}
      </h1>
      <div v-if="$slots.meta" class="project-page__meta"><slot name="meta" /></div>
      <!-- 页头上的按钮默认是小号的文字按钮；页头的主操作自己写
           color="primary" variant="flat"。 -->
      <div v-if="$slots.actions" class="project-page__actions">
        <v-defaults-provider :defaults="ACTION_DEFAULTS"><slot name="actions" /></v-defaults-provider>
      </div>
    </header>
    <div class="project-page__body">
      <div class="project-page__column" :class="`project-page__column--${width}`">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
.project-page__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 12px;
  height: var(--app-page-header-height);
  padding: 0 16px;
  border-bottom: var(--app-page-header-rule);
}
.project-page__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-page__parent {
  color: var(--muted);
  font-weight: 400;
  text-decoration: none;
  transition: color var(--dur-quick) var(--ease-standard);
}
.project-page__parent:hover {
  color: var(--ink);
}
.project-page__sep {
  margin-inline: 8px;
  color: var(--faint);
  font-weight: 400;
}
.project-page__meta {
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
.project-page__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  margin-inline-start: auto;
  overflow-x: auto;
  scrollbar-width: none;
}
.project-page__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.project-page__column {
  margin-inline: auto;
  padding: 24px 16px 48px;
}
.project-page__column--read {
  max-width: calc(var(--page-w) + 32px);
}
/* 满宽的那种自己管内边距：看板那几列各自滚动，得把高度一路钉到底。 */
.project-page__column--full {
  height: 100%;
  padding: 0;
}
</style>
