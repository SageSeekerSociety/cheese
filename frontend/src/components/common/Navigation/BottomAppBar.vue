<template>
  <v-bottom-navigation class="bottom-tabs" :elevation="0" bg-color="background" grow>
    <!-- 收到的就是手机那份清单（destinations.ts 的 tabItems），这里不再过滤：
         底栏装什么是清单的事，不是渲染的事。 -->
    <v-btn
      v-for="item in items"
      :key="item.key"
      :to="item.to"
      :active="item.match ? item.match(route.path) : undefined"
      :aria-label="item.badge ? `${item.title}（${item.badge}）` : undefined"
      @click="!item.to && item.action?.()"
    >
      <span class="bottom-tabs__icon">
        <v-icon v-if="item.icon">{{ item.icon }}</v-icon>
        <v-avatar v-else-if="item.img" size="24">
          <v-img :src="item.img"></v-img>
        </v-avatar>
        <!-- 件数角标：和桌面 rail 那一格同一颗（RailItem 的 .app-rail-item__badge）。
             画给眼睛的，读屏从上面的 aria-label 里听到件数。 -->
        <span v-if="item.badge" class="bottom-tabs__badge" aria-hidden="true">{{
          item.badge > 99 ? '99+' : item.badge
        }}</span>
      </span>
      <span class="bottom-tabs__label">{{ item.title }}</span>
    </v-btn>
  </v-bottom-navigation>
</template>

<script setup lang="ts">
import { toRefs } from 'vue'
import { useRoute } from 'vue-router'

import { NavItem } from './types'

const navBarProps = withDefaults(defineProps<{ items: NavItem[] }>(), {
  items: () => [],
})

const { items } = toRefs(navBarProps)
const route = useRoute()
</script>

<style scoped>
/* 当前这一格：图标琥珀、字用琥珀的文字色（--accent 本身写字对比度不够），不垫
   底色。Vuetify 默认给选中的那一格盖一层灰色遮罩，读起来像一块按下去没弹起来的
   按钮，而「你在这儿」是导航的当前位置，设计系统里那是琥珀的事（§1.6）。 */
.bottom-tabs :deep(.v-btn--active > .v-btn__overlay) {
  opacity: 0;
}
.bottom-tabs .v-btn--active .bottom-tabs__icon .v-icon {
  color: var(--accent);
}
.bottom-tabs .v-btn--active .bottom-tabs__label {
  color: var(--accent-ink);
}

.bottom-tabs__icon {
  position: relative;
  display: inline-flex;
}

/* 件数角标，和桌面 rail 那一颗同一套取色，理由见 RailItem.vue：暖色底上的字要一块
   不跟着主题翻白的深墨，外圈一道底栏底色让它压在图标角上时边缘清楚。 */
.bottom-tabs__badge {
  position: absolute;
  top: -4px;
  left: calc(100% - 6px);
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--warn);
  box-shadow: 0 0 0 2px var(--canvas);
  color: var(--inverse-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  text-align: center;
  pointer-events: none;
}

/* 手机底部安全区（Home 横杠 / 圆角）：不吃掉它，最下面那一排图标会被压住。
   桌面和没有安全区的设备上 env() 是 0。 */
.bottom-tabs {
  height: calc(56px + env(safe-area-inset-bottom)) !important;
  padding-bottom: env(safe-area-inset-bottom);
  /* 退回有底栏的一层时，它和页面同时就位：它是框，不跟着页面演（换页的那一下在
     App.vue 的 .page-enter--*）。Vuetify 默认让它从底下滑上来，那 0.2s 里内容区
     底部是一条空白。 */
  transition: none;
}
</style>
