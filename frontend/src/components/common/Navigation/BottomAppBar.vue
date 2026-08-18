<template>
  <v-bottom-navigation class="bottom-tabs" :elevation="0" bg-color="background" grow>
    <!-- 收到的就是手机那份清单（destinations.ts 的 tabItems），这里不再过滤：
         底栏装什么是清单的事，不是渲染的事。 -->
    <v-btn
      v-for="item in items"
      :key="item.key"
      :to="item.to"
      :active="item.match ? item.match(route.path) : undefined"
      @click="!item.to && item.action?.()"
    >
      <v-icon v-if="item.icon">{{ item.icon }}</v-icon>
      <v-avatar v-else-if="item.img" size="24">
        <v-img :src="item.img"></v-img>
      </v-avatar>
      <span>{{ item.title }}</span>
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
/* 手机底部安全区（Home 横杠 / 圆角）：不吃掉它，最下面那一排图标会被压住。
   桌面和没有安全区的设备上 env() 是 0。 */
.bottom-tabs {
  height: calc(56px + env(safe-area-inset-bottom)) !important;
  padding-bottom: env(safe-area-inset-bottom);
}
</style>
