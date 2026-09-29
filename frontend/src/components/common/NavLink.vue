<script setup lang="ts">
// 一颗「去某处」的链接：模板里该写 `<NavLink :to="...">` 的地方，就是原来写
// `<router-link :to="...">` 的地方。
//
// 为什么不直接用 `<router-link>`：它是**全局注册**的，只有装了 vue-router 的应用里
// 才解析得出来。没装路由的树（零插件的单测、脱离后端的预览宿主）里它是一条
// 「Failed to resolve component」的警告加一个画不出链接的空壳。于是每用到它的组件
// 都变成「必须先搭一套路由」—— 看板那一整排卡片、区块列表就是这样被拖下水的。
//
// 这里画的是真 `<a>`：装了路由就有 `href`（中键新开、右键复制链接、悬停看状态栏
// 都照旧），点了走 `useNavigation().navigate`，和 router-link 一样给组合键放行
// （⌘/Ctrl/Shift/中键交给浏览器自己开新标签）。**没装路由就不给 `href`** ——
// 那不是一条链接，所以也不进 Tab 顺序、指针不变形：一个看起来能点、按下去什么
// 都不发生的东西，比不画更糟（同 AdminKpiCard 文件头那条纪律）。
//
// class 由使用方给（`.akpi__link`、`.anl__row` 这些），透传到这一颗 `<a>` 上，
// 所以换过来的时候样式一行不用改。
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

const props = defineProps<{
  /** 去处。宿主没有路由、或者这条路不认识它，就画成不可点的。 */
  to: NavTarget
}>()

const nav = useNavigation()
const href = computed(() => nav?.href(props.to) ?? null)

function onClick(e: MouseEvent): void {
  if (!href.value || e.defaultPrevented || e.button !== 0) return
  // 带组合键的点击是「新开一个」的意思，交给浏览器按 href 去办。
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  e.preventDefault()
  nav?.navigate(props.to)
}
</script>

<template>
  <a
    :href="href ?? undefined"
    :style="href ? undefined : { cursor: 'default' }"
    :data-nav-inert="href ? undefined : ''"
    @click="onClick"
  >
    <slot />
  </a>
</template>
