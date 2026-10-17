// 单测里把「首帧画完了」这道门推开（`lib/afterFirstPaint.ts`）。
//
// 角标、装饰类的那一批请求都排在门后面，测试里没人推门 —— 没有 `main.ts`，也不会有
// 真跳转 —— 于是它们一次都不发。要它们照常发（或者要专门测它们**晚发**）就在这里开门：
// `holdFirstPaint()` 只把门装上、`releaseFirstPaint()` 推门，`openFirstPaint()` 是两步
// 一起，给「门开着」的用例用。
//
// 门是跟着 router 的 `afterEach` 排的，所以给一台只回调一次的假 router：注册即回调，
// 等于「这一次跳转落地了」；再让出两拍，`whenIdle` 在没有 `requestIdleCallback` 的环境
// （happy-dom 就是）下走它那条 `setTimeout(…, 0)`。
//
// **`vi.resetModules()` 之后要动态 import 这个文件**：门是模块内的一份状态，重置模块
// 注册表之后，静态 import 拿到的那一份和被测模块新拿到的那一份不是同一个，推了也没用。
import type { Router } from 'vue-router'

import { armAfterFirstPaint } from '@/lib/afterFirstPaint'

// 跳转落地那一刻回调 `armAfterFirstPaint` 收下的那个函数。`holdFirstPaint()` 装上之后
// 先攥在手里，`releaseFirstPaint()` 才放它落地——「这一次跳转还在路上」这一段，正是
// 角标那批该被挡住的时候。
let landed: (() => void) | null = null
let stop: (() => void) | null = null

/** 把门装上，但让这次跳转先不落地：给「门没开之前一次都不该发」那类用例。 */
export function holdFirstPaint(): void {
  stop?.()
  landed = null
  stop = armAfterFirstPaint({
    afterEach(callback: () => void) {
      landed = callback
      return () => {}
    },
  } as unknown as Router)
}

/** 让跳转落地，并且等它生效。 */
export async function releaseFirstPaint(): Promise<void> {
  landed?.()
  landed = null
  await new Promise((r) => setTimeout(r, 0))
  await new Promise((r) => setTimeout(r, 0))
}

/** 装作「首帧已经画完」，并且等它生效。 */
export async function openFirstPaint(): Promise<void> {
  holdFirstPaint()
  await releaseFirstPaint()
}

/** 把门关回去、并且摘掉：门是模块内一份状态，开着就一直开着，同一个文件里既要门开着、
 *  又要门关着的用例用它（静态 import 的那几份 spec 没法靠 `vi.resetModules()` 换一份）。 */
export function closeFirstPaint(): void {
  stop?.()
  stop = null
  landed = null
}
