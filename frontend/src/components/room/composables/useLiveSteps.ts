// 每位队友此刻在做的那一步，来自房间 socket 上的 `live` 帧（types/live.ts）。
//
// 和 useTypingPreview 同源、各管一半：那一个只认 `chat_send`，把正文长出来的那截
// 画成一条消息；这一个认**任何**工具调用，把「执行命令 · pnpm test」这样的当前
// 一步交给输入框下面那一行（MemberActivity）。两半都读同一批帧，但一个不碰另一个
// 的数据。
//
// 除了这一步，这里还记下每位队友最后一次来帧是什么时候。帧只在内容变化时才来，
// 所以「很久没有新帧」= 很久没有新输出 —— 那一行据此说「已 N 无新输出」。这一次
// 只是记账，不做任何事：谁都不因为这一步慢而被拦下。
//
// `reset()` 换房间时由房间壳调。

import type { WsServerFrame } from '../../../cx_types'

import { ref } from 'vue'

import { liveStep, stepText } from '../../../lib/liveStep'

export interface LiveStepState {
  /** 此刻在做的那一步，写成一句话；没在写就是 null。 */
  step: string | null
  /** 最后收到这个队友一帧的时刻（epoch 毫秒）。 */
  at: number
}

export function useLiveSteps() {
  // 队友 handle → 它此刻那一帧。
  const states = ref<Record<string, LiveStepState>>({})

  /** 房间壳处理完一帧之后交过来。只认 live 帧。 */
  function follow(frame: WsServerFrame) {
    if (frame.type !== 'live') return
    const agent = frame.agent
    if (!agent) return
    const parsed = liveStep(frame.blocks)
    states.value = { ...states.value, [agent]: { step: parsed ? stepText(parsed) : null, at: Date.now() } }
  }

  /** 换了房间，或断了线：上一位队友的那一步和这里无关了。 */
  function reset() {
    states.value = {}
  }

  return { states, follow, reset }
}
