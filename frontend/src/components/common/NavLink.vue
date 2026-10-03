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
// 所以换过来的时候样式一行不用改。`target="_blank"` 同理（协议那两句就是），而它
// 还多一层意思：**带 `target` 的点击也是「交给浏览器」**，见下面 `onClick`。
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

const props = defineProps<{
  /** 去处。宿主没有路由、或者这条路不认识它，就画成不可点的。 */
  to: NavTarget
  /** 换掉当前这一格而不是压上一条（设置里换栏、底栏换页签）。见 lib/backOut 文件头。 */
  replace?: boolean
}>()

const nav = useNavigation()
const href = computed(() => nav?.href(props.to) ?? null)

function onClick(e: MouseEvent): void {
  if (!href.value || e.defaultPrevented || e.button !== 0) return
  // 带组合键的点击是「新开一个」的意思，交给浏览器按 href 去办。
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  // `target="_blank"` 也是「新开一个」：替它 preventDefault 会把新标签页吞掉，
  // 原地跳走 —— 而用了 `_blank` 的地方（注册表单里的协议、重签协议那个弹窗）正是
  // 最不能原地跳走的两种。判据和 router-link 的 `guardEvent` 一字不差。
  if (/\b_blank\b/i.test((e.currentTarget as Element | null)?.getAttribute('target') ?? '')) return
  e.preventDefault()
  nav?.navigate(props.to, { replace: props.replace })
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
