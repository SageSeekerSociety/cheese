// 让一个由多块各自取数的页面「一次出现」。
//
// 设置页这种页面，每一块自己发请求、自己在回来时长高；上面那块一长高，下面所有块
// 一起往下跳——一个页面读下来是一串布局偏移（CLS）。页面提供一道闸：参与的各块在
// 首次取数期间占住它，页面在闸开之前只显示一个转圈，内容排在底下但不可见，全部到
// 齐后整页一起出现。闸最多关 maxWaitMs：一个慢请求不能让整页一直看不见，超时后已
// 经到的先出来，没到的块照常显示它自己的加载状态。
//
// 没有闸的地方（页面没提供），占闸是空操作，组件照旧自己加载自己显示。
import type { InjectionKey, Ref } from 'vue'

import { computed, getCurrentInstance, inject, onBeforeUnmount, provide, ref } from 'vue'

type Release = () => void

interface RevealGate {
  hold: () => Release
}

const GATE: InjectionKey<RevealGate> = Symbol('revealGate')

function noop() {}

export function provideRevealGate(maxWaitMs = 3000): { revealed: Ref<boolean>; hold: () => Release } {
  const pending = ref(0)
  const expired = ref(false)
  const timer = setTimeout(() => (expired.value = true), maxWaitMs)
  onBeforeUnmount(() => clearTimeout(timer))

  function hold(): Release {
    pending.value++
    let held = true
    return () => {
      if (!held) return
      held = false
      pending.value--
    }
  }

  provide(GATE, { hold })
  return { revealed: computed(() => expired.value || pending.value === 0), hold }
}

/** 在 setup 里调用：占住所在页面的闸，直到调用返回的 release 或组件卸载。重复 release 无害。 */
export function holdRevealGate(): Release {
  if (!getCurrentInstance()) return noop
  const gate = inject(GATE, null)
  if (!gate) return noop
  const release = gate.hold()
  onBeforeUnmount(release)
  return release
}
