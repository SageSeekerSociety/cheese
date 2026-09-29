<script setup lang="ts">
import { SPLIT_LIST_WIDTH } from '@/composables/useWorkspaceLayout'

// 两栏（平板）时左边那一栏的框：一条常驻的抽屉，里面放的就是手机上整页的那份话题
// 列表。不在两栏时它什么都不加，里面的东西原样渲染。
//
// order="-1"：Vuetify 按 order 从小到大给布局里的东西分地方，先分到的占满它那一边。
// 这一栏要比顶栏（0）先分，才是从顶到底的一整条，顶栏只盖右边的房间——话题头、←、
// 房间的操作都在那条顶栏上，它们说的是房间，不该横跨到列表上面。底栏比它还先（见
// BottomAppBar），所以底栏在的时候仍然铺满整个底边。
// 路由把参数整份当 props 递给 sidebar 视图（ProjectSidebar 用不上的那几个会落到这
// 里）；这个组件的根可能是一段片段，接不住，也不需要。
defineOptions({ inheritAttrs: false })
defineProps<{ active: boolean }>()
</script>

<template>
  <v-navigation-drawer
    v-if="active"
    permanent
    :width="SPLIT_LIST_WIDTH"
    order="-1"
    color="background"
    border="e-sm"
    class="split-list"
  >
    <slot />
  </v-navigation-drawer>
  <slot v-else />
</template>
