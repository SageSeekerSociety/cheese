// 界面导览要圈的那些控件：要当目标的组件挂 `v-guide-anchor="'某个名字'"` 把自己登记
// 进来，气泡（components/common/GuideBubble.vue）只问「这个名字登记了没有」。于是
// 「控件不在这页上就不冒气泡」是默认行为，换页面、开菜单也会自己重新找目标。
import type { Directive, Ref } from 'vue'

import { ref } from 'vue'

const anchors = new Map<string, HTMLElement>()
/** 登记表改过几次。气泡读它，好知道该重新找一次目标。 */
const anchorRevision = ref(0)

/**
 * 这个名字现在指着哪个控件；没登记就是 null。
 *
 * 不在这里判「它还在不在页面上」：keep-alive 收起来的组件不会走 `unmounted`，登记
 * 表里会留一个已经不挂在文档上的元素。不在页面上（或 `display:none`）的元素量出来
 * 是 0×0，气泡本来就不指它，见 GuideBubble 的 `measure()`。
 */
export function guideAnchor(name: string): HTMLElement | null {
  return anchors.get(name) ?? null
}

export function useGuideAnchorRevision(): Ref<number> {
  return anchorRevision
}

/** 给测试用：把登记表清回初始值。 */
export function __resetGuideAnchorsForTests(): void {
  anchors.clear()
  anchorRevision.value = 0
}

function register(name: string, el: HTMLElement) {
  if (!name) return
  anchors.set(name, el)
  anchorRevision.value += 1
}

function unregister(name: string | null, el: HTMLElement) {
  if (!name) return
  // 同一个名字被两个元素登记过（旧的还没卸、新的已经挂上）时，只清掉自己那一个。
  if (anchors.get(name) !== el) return
  anchors.delete(name)
  anchorRevision.value += 1
}

/**
 * 把一个控件登记成导览能指的目标：`<button v-guide-anchor="'composer-summon'">`。
 * 写成指令而不是模板 ref：指令拿到的 `el` 永远是 DOM 元素，挂在组件上也行。
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
