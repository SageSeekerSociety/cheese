<template>
  <!-- The screen's own title row; the route's title when the screen declared none
       (composables/useScreenHeader.ts). -->
  <div v-if="header" class="screen-title">
    <component :is="header.content.component" v-bind="header.content.props" />
  </div>
  <!-- A bare title is the toolbar's own child, so Vuetify's toolbar spacing applies to
       it as before. 标题读页头那一号字 (.t-title 15/600)：屏幕上这条横条和页内页头是
       同一条，Vuetify 默认的 20px 会让它们看起来是两种东西。 -->
  <v-app-bar-title v-else class="t-title">{{ fallback }}</v-app-bar-title>
</template>

<script setup lang="ts">
// 手机顶栏中间那一格：这一屏声明的头部（composables/useScreenHeader.ts，顶栏递进来），
// 没声明就是路由的标题。
import type { ScreenHeader } from '@/composables/useScreenHeader'

defineOptions({ name: 'ScreenHeaderTitle' })

defineProps<{
  /** 这一屏声明的头部；null 是没声明。 */
  header: ScreenHeader | null
  /** 没声明时写的：路由的标题。 */
  fallback: string
}>()
</script>

<style scoped>
/* 吃掉中间所有剩余宽度；不写 min-width 的话，里面的长标题会把右边的头像顶出去。 */
.screen-title {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
  height: 100%;

  /* 这一屏自己的页头和壳的操作之间留 4px。页头最后一颗常是 40px 的图标按钮，
     `.tap-target` 把它能点的范围撑到 44，右边那 2px 会落进紧挨着的下一页操作里；
     房间页的「更多」和芝士入口就是这么盖住的（docs/design-system.md §10.1：间距
     不够时拉开间距，不要只撑其中一颗；量它的是 layout-invariants 的 44×44 那条）。 */
  padding-inline-end: 4px;
}

/* Vuetify 的工具栏标题自带 20px/400，比页内页头的标题 (.t-title 15/600) 大一号——
   而在手机上这两条横条是同一条东西在换内容，一页大一号就看得出是两套。加类名
   压过它，而不是去改 .t-title：那一号字是设计 token，页头都读它。 */
.v-toolbar-title.t-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}
</style>
