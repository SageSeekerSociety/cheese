<template>
  <!-- The screen's own title row, or its title and the line under it; the route's
       title when the screen declared nothing (composables/useScreenHeader.ts). -->
  <div v-if="header?.content" class="screen-title screen-title--row">
    <component :is="header.content.component" v-bind="header.content.props" />
  </div>
  <!-- 标题读页头那一号字 (.t-title 15/600)：屏幕上这条横条和页内页头是同一条，
       Vuetify 默认的 20px 会让它们看起来是两种东西。 -->
  <div v-else class="screen-title">
    <v-app-bar-title class="t-title">{{ header?.title ?? fallback }}</v-app-bar-title>
    <div v-if="header?.subtitle" class="screen-title__sub t-meta">{{ header.subtitle }}</div>
  </div>
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
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  height: 100%;
}
.screen-title--row {
  flex-direction: row;
  align-items: center;
}
.screen-title :deep(.v-toolbar-title__placeholder),
.screen-title__sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.screen-title__sub {
  margin-inline-start: 16px;
  color: var(--muted);
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
