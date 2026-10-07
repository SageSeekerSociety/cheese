// 新用户那串气泡浮层的两个把手：谁点过「跳过引导」，以及气泡要指的那颗按钮现在在
// 不在页面上。
//
// 跳过记在本机（localStorage），不落库：这是一个人的选择，不是项目的数据。第 1 步
// 「建项目」发生在还没有项目的时候，那时没有项目可挂，所以这一个开关只能按人记；
// 清单卡上那个按项目记的「不再提示」（useGettingStarted）是另一回事，它额外把这里
// 也打上。
//
// 锚点走一张模块级的登记表：要当目标的组件挂 `v-guide-anchor="'某个名字'"` 把自己
// 登记进来，浮层只问「这个名字登记了没有」。于是「按钮不在这页上就不冒气泡」是这套
// 东西的默认行为，不用每个调用处各判一次；换页面、开菜单也会自己重新找目标。
import type { Directive, Ref } from 'vue'

import { ref } from 'vue'

const SKIP_KEY = 'cheese.startGuide.skipped'

function readSkipped(): boolean {
  try {
    return localStorage.getItem(SKIP_KEY) === '1'
  } catch {
    // 读不到（无痕模式之类）就当没跳过：多提示一次，好过永远不再提示。
    return false
  }
}

const skipped = ref(readSkipped())

/** 气泡浮层那个开关。「跳过引导」和清单卡上的「不再提示」都打它。 */
export function useStartGuide(): { skipped: Ref<boolean>; skip: () => void } {
  function skip() {
    skipped.value = true
    try {
      localStorage.setItem(SKIP_KEY, '1')
    } catch {
      // 存不进去：这一次先藏起来，下次进来还会出现。
    }
  }
  return { skipped, skip }
}

/**
 * 把这一份模块级状态清回初始值，给测试用（范本：`lib/pwaInstall.ts` 的
 * `__resetInstallPromptForTests`）。会话里没有第二个调用处：这一份状态本就是全应用
 * 共用的，正常路径上没有什么该把它清掉。
 */
export function __resetStartGuideForTests(): void {
  skipped.value = false
  anchors.clear()
  anchorRevision.value = 0
}

const anchors = new Map<string, HTMLElement>()
/** 登记表改过几次。浮层读它，好知道该重新找一次目标。 */
const anchorRevision = ref(0)

/**
 * 这个名字现在指着哪颗按钮；没登记就是 null。
 *
 * 不在这里判「它还在不在页面上」：keep-alive 收起来的组件不会走 `unmounted`，登记
 * 表里会留一个已经不挂在文档上的元素。这种事交给量尺寸那一头——不在页面上（或者被
 * `display:none` 藏起来）的元素量出来是 0×0，浮层本来就不该指它，见 StartGuide 的
 * `measure()`。
 */
export function guideAnchor(name: string): HTMLElement | null {
  return anchors.get(name) ?? null
}

export function useGuideAnchorRevision(): Ref<number> {
  return anchorRevision
}

function register(name: string, el: HTMLElement) {
  if (!name) return
  anchors.set(name, el)
  anchorRevision.value += 1
}

function unregister(name: string | null, el: HTMLElement) {
  // 名字可能没有：指令的 `updated` 拿到的是 `oldValue`，第一次渲染时它是 null。
  if (!name) return
  // 同一个名字被两个元素登记过（旧的还没卸、新的已经挂上）时，只清掉自己那一个。
  if (anchors.get(name) !== el) return
  anchors.delete(name)
  anchorRevision.value += 1
}

/**
 * 把一颗按钮登记成浮层能指的目标：`<button v-guide-anchor="'composer-attach'">`。
 *
 * 写成指令而不是模板 ref：指令拿到的 `el` 永远是 DOM 元素，所以把它挂在一个组件
 * （`<v-btn>`、`<v-card>`）上也行，不用再猜拿到的是元素还是组件实例。
 */
export const vGuideAnchor: Directive<HTMLElement, string> = {
  mounted(el, binding) {
    register(binding.value, el)
  },
  updated(el, binding) {
    if (binding.value === binding.oldValue) return
    unregister(binding.oldValue, el)
    register(binding.value, el)
  },
  unmounted(el, binding) {
    unregister(binding.value, el)
  },
}
