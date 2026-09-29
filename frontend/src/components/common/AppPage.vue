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
// 板）。页面不再各自写一个数字。
import type { NavTarget } from '@/lib/navTarget'

import { useDisplay } from 'vuetify'

import { headerCommands } from '@/commands'
import NavLink from '@/components/common/NavLink.vue'

withDefaults(
  defineProps<{
    title: string
    width?: 'read' | 'full'
    // 这一页是另一页里的一项（成员名册里的一个人）：页头写成「成员 / 名字」，前
    // 一段点回去。
    parent?: { label: string; to: NavTarget }
  }>(),
  { width: 'read', parent: undefined }
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
    <header v-if="mdAndUp || $slots.meta" class="app-page__head">
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
          <v-btn
            v-if="command.header?.iconOnly"
            :icon="command.icon"
            variant="text"
            size="small"
            :to="command.to"
            :loading="command.loading"
            :disabled="command.disabled"
            :aria-label="command.title"
            :title="command.title"
            @click="command.run?.()"
          />
          <v-btn
            v-else
            :prepend-icon="command.icon"
            :color="command.header?.accent ? 'primary' : undefined"
            :variant="command.header?.accent ? 'flat' : 'text'"
            size="small"
            :to="command.to"
            :loading="command.loading"
            :disabled="command.disabled"
            @click="command.run?.()"
          >
            {{ command.title }}
          </v-btn>
        </template>
      </div>
    </header>
    <div class="app-page__body">
      <div class="app-page__column" :class="`app-page__column--${width}`">
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
.app-page__column--read {
  max-width: calc(var(--page-w) + 32px);
}
/* 满宽的那种自己管内边距：看板那几列各自滚动，得把高度一路钉到底。 */
.app-page__column--full {
  height: 100%;
  padding: 0;
}
</style>
