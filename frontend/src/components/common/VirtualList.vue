<script lang="ts">
// 通用虚拟列表：一列**超过** `threshold` 才交给 virtua，以下整列渲染（门槛的道理见
// `src/lib/virtualList.ts`：FLIP 换位和「先后」要靠整列都在 DOM 里才演得出来）。
//
//   <VirtualList :items="rows" :item-key="(r) => r.id" :scroll-parent="railScroll" :estimated-size="44">
//     <template #item="{ item }"><MyRow :row="item" /></template>
//   </VirtualList>
//
// 一个 item 槽只画**一个**根元素：虚拟化时 virtua 给每一行套一层定位用的盒子，那一层
// 也带着行的 key；槽里出来两个根，就没法给它们同一个身份了。
//
// 虚拟化要知道**谁在滚**，所以滚动容器是挨着门槛的第二个条件：外面有（话题列表的
// `.rail-scroll`——它上面还有置顶行和组头，不是这一列自己的；看板底下那一列自己有
// max-height 和 overflow）就虚拟化，没有就照旧整列画。不替外面猜一个（自己起一个滚动
// 盒子的话，滚的就不是外面那层了，置顶行和组头会留在原地不动）。
//
// 滚动容器是个 DOM 元素，而模板 ref 要等挂完才落地，所以一份**刚出现的长列表**第一帧
// 会整列画一遍、下一帧才交给 virtua。同一轮 flush 里就换完，屏幕上只看得到后一帧（不
// 闪）；代价是那一列 DOM 白建一次。话题列表的数据是异步来的，碰不到这一帧；看板上展开
// 「已完成」时会碰上，那一帧整列画出来也正好是「展开就看到全部」。
import type { Component, PropType, VNode } from 'vue'

import { cloneVNode, Comment, computed, defineComponent, h, isVNode, ref, Text, TransitionGroup } from 'vue'
import { Virtualizer } from 'virtua/vue'

import { VIRTUAL_LIST_THRESHOLD } from '@/lib/virtualList'

/** virtua 的组件类型声明的是一段「构造签名」（`new <T>(props) => 实例`），喂给 `h()` 会被
 *  当成类组件，于是按实例成员来校验 props。这里按 Vue 自己的 `Component` 收进来，`h()`
 *  就只按槽和 props 走——运行时一模一样。 */
const Virtualized = Virtualizer as unknown as Component

/** virtua 的 `scrollToIndex` 选项。在这儿自己写一遍，是为了不为了一个类型去摸它
 *  带着 React 那一半的包入口。 */
export interface VirtualListScrollOptions {
  align?: 'start' | 'center' | 'end' | 'nearest'
  smooth?: boolean
  offset?: number
}

/** 虚拟列表露给外面的一点点：按序号滚到某一行。 */
export interface VirtualListHandle {
  scrollToIndex: (index: number, options?: VirtualListScrollOptions) => void
}

export default defineComponent({
  name: 'VirtualList',
  props: {
    /** 完整的一份数据，不是窗口里那几行。 */
    items: { type: Array as PropType<readonly unknown[]>, required: true },
    /** 行的稳定标识，和 v-for 的 key 同义。 */
    itemKey: { type: Function as PropType<(item: unknown, index: number) => string | number>, required: true },
    /** 超过这个行数才虚拟化。 */
    threshold: { type: Number, default: VIRTUAL_LIST_THRESHOLD },
    /** 一行的估计高度（px）。真实高度 virtua 自己量，这个只用来算第一屏的窗口。 */
    estimatedSize: { type: Number, default: 40 },
    /** 视口外多留几行，快滚时不容易看见空白。 */
    bufferSize: { type: Number, default: 300 },
    /** 外面的滚动容器。给了才虚拟化——没给就不知道该按谁的窗口算，照旧整列画。 */
    scrollParent: { type: Object as PropType<HTMLElement | null>, default: null },
    /** 行会换位置（按最近动静重排）时打开：virtua 用它少算几步位移。 */
    shift: { type: Boolean, default: false },
    /** 无论滚到哪儿都留在 DOM 里的行（序号）。选中的那一行必须留着，否则它一滚出窗口
     *  就从树上摘了，光标也跟着没。 */
    keepMounted: { type: Array as PropType<readonly number[]>, default: undefined },
    /** 整列渲染时那一层的过渡名（FLIP）。虚拟化时不用它——见上面门槛那段。 */
    transition: { type: String, default: '' },
    /** 过渡那一层换 key 的时刻：换项目 = 换了一整份列表，不是这份列表在重排，不演。 */
    transitionKey: { type: [String, Number] as PropType<string | number>, default: undefined },
  },
  setup(props, { slots, expose }) {
    const list = ref<VirtualListHandle | null>(null)
    // 两个条件都要：行数过门槛，且知道谁在滚（见文件头）。
    const virtualized = computed(() => props.items.length > props.threshold && props.scrollParent != null)

    function scrollToIndex(index: number, options?: VirtualListScrollOptions) {
      list.value?.scrollToIndex(index, { align: 'nearest', ...options })
    }

    // 键盘走到窗口外的行上（Tab 过去、长按菜单把焦点还回那一行）：把那一行带进视口。
    // 只有虚拟化时那一行的盒子才带 `data-vlist-index`，整列渲染时两件事都不必做。
    function onFocusIn(event: FocusEvent) {
      const host = (event.target as HTMLElement | null)?.closest?.('[data-vlist-index]')
      const index = host?.getAttribute('data-vlist-index')
      if (index != null) scrollToIndex(Number(index))
    }

    /** 整列渲染：和没接虚拟列表时画的一模一样——同一层 TransitionGroup、同一批 key。 */
    function plainRows(): VNode[] {
      const rows: VNode[] = []
      props.items.forEach((item, index) => {
        const rendered = slots.item?.({ item, index }) ?? []
        // 槽里只认第一个真元素：注释/空白（v-if 落空、模板里的换行）不是行。
        const node = rendered.find((child) => isVNode(child) && child.type !== Comment && child.type !== Text)
        if (node) rows.push(cloneVNode(node, { key: props.itemKey(item, index) }))
      })
      return rows
    }

    expose({ scrollToIndex })

    return () => {
      if (!virtualized.value) {
        const rows = plainRows()
        return props.transition
          ? h(TransitionGroup, { key: props.transitionKey, name: props.transition }, { default: () => rows })
          : rows
      }
      // 走到这儿 `scrollParent` 一定在（`virtualized` 把它算进了条件）。
      const render = { default: (row: { item: unknown; index: number }) => slots.item?.(row) }
      return h(
        Virtualized,
        {
          ref: list,
          scrollRef: props.scrollParent,
          data: props.items,
          itemSize: props.estimatedSize,
          bufferSize: props.bufferSize,
          shift: props.shift,
          keepMounted: props.keepMounted,
          // 每一行的盒子上记着序号：键盘走到窗口外的行上时（见 onFocusIn）按它滚过去。
          itemProps: (_item: unknown, index: number) => ({ 'data-vlist-index': String(index) }),
          onFocusin: onFocusIn,
        },
        render
      )
    }
  },
})
</script>
