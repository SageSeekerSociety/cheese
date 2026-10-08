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
// 容器不归它管（话题列表的行要塞进既有的 `<v-list>` 里），但行外面那一层可以要：看板
// 那一列是 `ul > li`，`:item-as="li"`（配 `item-role="listitem"`）让两种形态套出来的
// 都是同一个 `li`——虚拟化前后是同一套结构，不是「虚拟化了就少了语义」。virtua 自己
// 那层定位盒子是 `role="presentation"`（见虚拟化那一段）：它不是内容，夹在 `ul` 和
// `li` 中间会把列表语义切断，得从无障碍树上消失。
//
// 光标停在某一行上时，那一行要一直在 DOM 里：它一滚出窗口就被摘掉的话，焦点会掉回
// body，接着打字就不知道打到哪儿去了。所以 focusin 时记下那一行、并进交给 virtua 的
// keepMounted，focusout 出了这份列表就放掉（见 `keptIndices`）。记的是行的 key 不是
// 序号——列表按最近动静重排时序号会换人。
//
// 顺着 Tab 走**走不到还没挂出来的行**（虚拟化只挂窗口附近那些）：要把整列收成一个
// Tab 停靠点、再用上下键在里面走（roving tabindex）才做得到，这份实现有意不做——
// 行内的按钮（行尾那颗 ⋯）本来就在同一个 Tab 顺序里，够用。
//
// 虚拟化要知道**谁在滚**，所以滚动容器是挨着门槛的第二个条件：外面有（话题列表的
// `.rail-scroll`——它上面还有置顶行和组头，不是这一列自己的；看板底下那一列自己有
// max-height 和 overflow）就虚拟化，没有就照旧整列画。不替外面猜一个（自己起一个滚动
// 盒子的话，滚的就不是外面那层了，置顶行和组头会留在原地不动）。
//
// 共享外面的滚动容器，就得告诉 virtua 这份列表**离滚动内容起点有多远**（virtua 的
// `startMargin`）：virtua 手上只有滚动容器的 `scrollTop`，它默认列表是从 0 开始的；
// 话题栏里这一列长在置顶行、组头、上面几组后面，离起点好几千像素——不告诉它，就会
// 把「滚动容器已经滚了这么深」当成「这份列表已经滚了这么深」，窗口一路算偏，行被画到
// 视口**下面**去，屏幕上从某个位置往下全是空白（owner 报的「长度不对、上面一片空白」）。
// 这个距离是量出来的（`measureStartMargin`），不是传进来的常量：置顶行几条、上面几组
// 开着还是收着，都会让它变。看板那一列自己就是滚动容器、diff 顶上只有一点 padding、
// 资料库顶上还有搜索框——量出来各是各的对。
//
// 滚动容器是个 DOM 元素，而模板 ref 要等挂完才落地：组件**第一次**渲染的时候宿主的
// 模板 ref 还是 null，而渲染函数是在挂载那一趟里就跑完的。所以一份一出现就过门槛的
// 长列表（宿主手上已经有数据了，比如离开再回到这个项目），第一帧**不知道谁在滚**。
//
// 那一帧不整列画（`waitingForScrollParent`）：virtua 一接手就会把那一列全扔掉重建，
// 全建一遍等于白建（前端的旧版本就是这么干的，报告里「列表出现后又重建」有一半是它）。
// 留下一个按估计行高撑起来的空盒子过渡这一帧——空着会让宿主量出「这一页没填满滚动
// 容器」，反过来再取一页数据（`useLibraryPages` 就是这么做的），所以高度得垫上。
// 挂载后第一趟 flush 一过就知道宿主到底给不给容器了：给过就交给 virtua，一直没给
// （宿主本来就没有滚动容器，见上）就照旧整列画。
import type { Component, PropType, VNode } from 'vue'

import {
  cloneVNode,
  Comment,
  computed,
  defineComponent,
  h,
  isVNode,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  Text,
  TransitionGroup,
  watch,
} from 'vue'
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
    /** 无论滚到哪儿都留在 DOM 里的行（序号）。选中的那一行必须留着，否则它一滚出窗口
     *  就从树上摘了，光标也跟着没。 */
    keepMounted: { type: Array as PropType<readonly number[]>, default: undefined },
    /** 整列渲染时那一层的过渡名（FLIP）。虚拟化时不用它——见上面门槛那段。 */
    transition: { type: String, default: '' },
    /** 过渡那一层换 key 的时刻：换项目 = 换了一整份列表，不是这份列表在重排，不演。 */
    transitionKey: { type: [String, Number] as PropType<string | number>, default: undefined },
    /** 每一行的外壳标签（虚拟化时就是 virtua 的 `item`）。不给就直接画槽里的根——
     *  话题列表的行要塞进既有的 `<v-list>`，不该再套一层。 */
    itemAs: { type: String, default: '' },
    /** 外壳的 role。看板那一列是 `ul > li`，虚拟化时 virtua 会在中间多套一层定位用的
     *  generic div，列表语义就靠这个显式的 `listitem` 撑着（Safari 在 `list-style: none`
     *  时本来也会把列表语义抹掉）。 */
    itemRole: { type: String, default: '' },
  },
  setup(props, { slots, expose }) {
    // 组件实例上的那只手，外加 `$el`：virtua 的根就是它那层列表容器，量 `startMargin`
    // 量的是它。`$el` 是 Vue 公共实例属性（`publicPropertiesMap`），从 expose 代理上读
    // 不会报警告，所以不必为了量偏移再套一层自己的壳（套了反而多一层没用的节点）。
    const list = ref<(VirtualListHandle & { $el?: HTMLElement | null }) | null>(null)
    // 光标所在的那一行（按 itemKey 记）。见文件头「光标停在某一行上」那段。
    const focusedKey = ref<string | number | null>(null)
    // 两个条件都要：行数过门槛，且知道谁在滚（见文件头）。
    const virtualized = computed(() => props.items.length > props.threshold && props.scrollParent != null)

    // 挂载后第一趟 flush 过了没有。模板 ref 是在挂载那一趟里落地的，过了这一趟还看不到
    // 滚动容器，就是宿主本来就没有（见文件头）。
    const settledAfterMount = ref(false)
    onMounted(() => {
      void nextTick(() => {
        settledAfterMount.value = true
      })
    })
    // 一出现就过门槛、而这一帧还不知道谁在滚：这一帧谁都不画（见文件头）。
    const waitingForScrollParent = computed(
      () => props.items.length > props.threshold && props.scrollParent == null && !settledAfterMount.value
    )

    // 这份列表离滚动内容起点有多远（px）——交给 virtua 当 `startMargin`。默认 0 直到量到
    // 真的值；量法和为什么必须量见文件头那段。
    const startMargin = ref(0)

    /** 递给 virtua 的常驻行：外面点名要留的（选中的、开着菜单的……）加上光标所在的那一行。 */
    const keptIndices = computed<readonly number[] | undefined>(() => {
      const key = focusedKey.value
      if (key == null) return props.keepMounted
      const kept = props.keepMounted ?? []
      const index = props.items.findIndex((item, at) => props.itemKey(item, at) === key)
      // 那一行已经不在这一列里了（被筛掉、归档了），或者本来就要留着：外面那份照传。
      if (index < 0 || kept.includes(index)) return props.keepMounted
      return [...kept, index]
    })

    function scrollToIndex(index: number, options?: VirtualListScrollOptions) {
      list.value?.scrollToIndex(index, { align: 'nearest', ...options })
    }

    // 键盘走到窗口外的行上（Tab 过去、长按菜单把焦点还回那一行）：把那一行带进视口，
    // 并记住是它——光标留在上面期间它不能被摘掉。
    // 只有虚拟化时那一行的盒子才带 `data-vlist-index`，整列渲染时两件事都不必做。
    function onFocusIn(event: FocusEvent) {
      const host = (event.target as HTMLElement | null)?.closest?.('[data-vlist-index]')
      const index = host?.getAttribute('data-vlist-index')
      if (index == null) return
      const at = Number(index)
      const item = props.items[at]
      if (item !== undefined) focusedKey.value = props.itemKey(item, at)
      scrollToIndex(at)
    }

    // 焦点挪到列表里的另一行（Tab、点另一行、进那一行的按钮）就留着；出了这份列表
    // （掉到 body、去别的控件）就放掉——不然那一行会被一直钉在 DOM 里。
    function onFocusOut(event: FocusEvent) {
      const next = event.relatedTarget as HTMLElement | null
      if (next?.closest?.('[data-vlist-index]')) return
      focusedKey.value = null
    }

    // 量这份列表离滚动内容起点有多远（见文件头「共享外面的滚动容器」那段）。量的是
    // 「容器盒子的顶」减「滚动容器内容盒的顶（= 边框盒顶 + 上边框）」再加 `scrollTop`
    // ——与当前滚到哪儿无关，所以滚动中反复量也永远是同一个数，不会把状态搅动起来。
    // 变了才写回（一行行高、上面一组的开合都会让它变），拿整数像素：亚像素抖动不值得重算。
    function measureStartMargin() {
      const parent = props.scrollParent
      const element = list.value?.$el
      if (!parent || !element) return
      const value = Math.max(
        0,
        Math.round(
          element.getBoundingClientRect().top - parent.getBoundingClientRect().top - parent.clientTop + parent.scrollTop
        )
      )
      if (value !== startMargin.value) startMargin.value = value
    }

    // 滚动、改尺寸、结构变化都只攒一帧量一次：这些都是「上面那截可能变了」的信号，量本身
    // 只是两次 `getBoundingClientRect`，但不必每个事件都量。
    let marginFrame = 0
    function scheduleMeasure() {
      if (marginFrame) return
      marginFrame = requestAnimationFrame(() => {
        marginFrame = 0
        measureStartMargin()
      })
    }

    let marginResize: ResizeObserver | undefined
    let marginMutations: MutationObserver | undefined
    function startMeasuring() {
      stopMeasuring()
      const parent = props.scrollParent
      if (!virtualized.value || !parent) return
      if (typeof ResizeObserver !== 'undefined') {
        marginResize = new ResizeObserver(scheduleMeasure)
        marginResize.observe(parent)
      }
      if (typeof MutationObserver !== 'undefined') {
        // 只看滚动容器的**直接子元素**：组头开合、上面几组的增删都落在这层（`v-list` 是
        // 直接子元素），所以这份列表的位置一变就被叫醒。virtua 自己挂/摘行发生在列表
        // 容器**里面**，不在这一层——不会每滚一下就把我们叫醒一次。
        marginMutations = new MutationObserver(scheduleMeasure)
        marginMutations.observe(parent, { childList: true })
      }
      document.addEventListener('scroll', scheduleMeasure, true)
      window.addEventListener('resize', scheduleMeasure)
      measureStartMargin()
    }
    function stopMeasuring() {
      marginResize?.disconnect()
      marginResize = undefined
      marginMutations?.disconnect()
      marginMutations = undefined
      document.removeEventListener('scroll', scheduleMeasure, true)
      window.removeEventListener('resize', scheduleMeasure)
      if (marginFrame) {
        cancelAnimationFrame(marginFrame)
        marginFrame = 0
      }
    }

    // 挂完先量一次（那时候上面的置顶行、组头都已经在 DOM 里了）。滚动容器是模板 ref，
    // 挂完那一帧才落地（见文件头），所以真正开始虚拟化（`virtualized` 翻真）时再量一次。
    onMounted(startMeasuring)
    watch(virtualized, () => void nextTick(startMeasuring), { flush: 'post' })
    onBeforeUnmount(stopMeasuring)

    /** 整列渲染：和没接虚拟列表时画的一模一样——同一层 TransitionGroup、同一批 key。 */
    function plainRows(): VNode[] {
      const rows: VNode[] = []
      props.items.forEach((item, index) => {
        const rendered = slots.item?.({ item, index }) ?? []
        // 槽里只认第一个真元素：注释/空白（v-if 落空、模板里的换行）不是行。
        const node = rendered.find((child) => isVNode(child) && child.type !== Comment && child.type !== Text)
        if (!node) return
        const key = props.itemKey(item, index)
        // 要外壳就套一层（`li` 这类）；key 落在外壳上，换位时 Vue 认得出是同一行。
        rows.push(
          props.itemAs
            ? h(props.itemAs, { key, ...(props.itemRole ? { role: props.itemRole } : {}) }, [node])
            : cloneVNode(node, { key })
        )
      })
      return rows
    }

    expose({ scrollToIndex })

    return () => {
      if (waitingForScrollParent.value) {
        // 空盒子（见文件头）：撑住估计的高度，别让宿主把这一帧当成「这一页没填满滚动
        // 容器」。外壳标签跟着 `itemAs` 走——宿主是 `ul` 的时候这一个也得是 `li`。
        return h(props.itemAs || 'div', {
          'aria-hidden': 'true',
          style: { height: `${props.items.length * props.estimatedSize}px` },
        })
      }
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
          // 这份列表离滚动内容起点有多远（见文件头「共享外面的滚动容器」那段）：置顶行、
          // 组头、上面几组都算在里面。virtua 的容器驱动自己不量这个，只能我们告诉它。
          startMargin: startMargin.value,
          bufferSize: props.bufferSize,
          keepMounted: keptIndices.value,
          item: props.itemAs || undefined,
          // virtua 套出来的那一层是**定位用的盒子**，不是内容：看板那一列虚拟化以后是
          // `ul > div > li`，中间这层 generic div 会把 ul 的列表语义切断。让它在无障碍
          // 树上消失，`ul` 和 `li` 就还是父子（话题列表那边同理，v-list 的列表/导航语义
          // 也靠这个撑着）。它不可聚焦，presentation 用了不会被忽略。
          role: 'presentation',
          // 每一行的盒子上记着序号：键盘走到窗口外的行上时（见 onFocusIn）按它滚过去。
          // virtua 是按 `{ item, index }` 一个对象调过来的（见 ItemProps），不是一个一个传。
          itemProps: ({ index }: { item: unknown; index: number }) => {
            const attrs: Record<string, string> = { 'data-vlist-index': String(index) }
            if (props.itemRole) attrs.role = props.itemRole
            return attrs
          },
          onFocusin: onFocusIn,
          onFocusout: onFocusOut,
        },
        render
      )
    }
  },
})
</script>
