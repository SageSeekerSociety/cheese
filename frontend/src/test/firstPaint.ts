// 单测里把「首帧画完了」这道门推开（`lib/afterFirstPaint.ts`）。
//
// 角标、装饰类的那一批请求都排在门后面，测试里没人推门 —— 没有 `main.ts`，也不会有
// 真跳转 —— 于是它们一次都不发。要它们照常发（或者要专门测它们**晚发**）就在这里开门。
//
// 门是跟着 router 的 `afterEach` 排的，所以给一台只回调一次的假 router：注册即回调，
// 等于「这一次跳转落地了」；再让出两拍，`whenIdle` 在没有 `requestIdleCallback` 的环境
// （happy-dom 就是）下走它那条 `setTimeout(…, 0)`。
//
// **`vi.resetModules()` 之后要动态 import 这个文件**：门是模块内的一份状态，重置模块
// 注册表之后，静态 import 拿到的那一份和被测模块新拿到的那一份不是同一个，推了也没用。
import type { Router } from 'vue-router'

import { armAfterFirstPaint } from '@/lib/afterFirstPaint'

/** 装作「首帧已经画完」，并且等它生效。 */
export async function openFirstPaint(): Promise<void> {
  armAfterFirstPaint({
    afterEach(callback: () => void) {
      callback()
      return () => {}
    },
  } as unknown as Router)
  await new Promise((r) => setTimeout(r, 0))
  await new Promise((r) => setTimeout(r, 0))
}
