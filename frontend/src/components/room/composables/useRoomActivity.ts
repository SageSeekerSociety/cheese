/**
 * 此刻谁在这个房间里忙：在输入框里打字的人、有一轮在跑的 AI 队友。
 *
 * 房间自己没有状态，有的是成员在做什么。后端在房间的 socket 上报 `activity`（一位
 * 成员开始 / 停下）和连上时的 `activity_snapshot`；这里只记账，不认识 socket。打字
 * 是短命的：帧上带着 `expires_in`，到点没有新的一下就自己撤掉，不等一帧「停了」。
 *
 * 另一半是我自己打字：输入框的内容变了，至多每三秒告诉房间一次「我在打字」；清空了
 * 就说一声停了。
 */

import type { WsClientMessage } from '../../../cx_types'
import type { MemberActivity } from '../../../lib/memberActivity'

import { computed, onScopeDispose, ref } from 'vue'

/** 我在打字时，多久提醒房间一次。后端那边一下管五秒（`agent/realtime/activity.py`）。 */
export const TYPING_PING_MS = 3_000

export function useRoomActivity(options: {
  /** 我是谁：自己的打字不画给自己看。 */
  me: string
  /** 往房间的 socket 说一句；没连上就什么都不发。 */
  send: (message: WsClientMessage) => void
}) {
  // 上一次告诉房间「我在打字」是什么时候；0 = 没在打。
  let lastPing = 0
  // `${member}:${kind}` → 那一条。
  const entries = ref<Record<string, MemberActivity>>({})
  const expiry = new Map<string, ReturnType<typeof setTimeout>>()

  function forget(key: string) {
    clearTimeout(expiry.get(key))
    expiry.delete(key)
    if (!(key in entries.value)) return
    const next = { ...entries.value }
    delete next[key]
    entries.value = next
  }

  function hold(entry: MemberActivity) {
    const key = `${entry.member}:${entry.kind}`
    clearTimeout(expiry.get(key))
    expiry.delete(key)
    entries.value = { ...entries.value, [key]: entry }
    if (entry.expires_in)
      expiry.set(
        key,
        setTimeout(() => forget(key), entry.expires_in * 1000)
      )
  }

  /** 一帧 `activity`。 */
  function apply(frame: MemberActivity & { active: boolean }) {
    const { member, kind, since, expires_in } = frame
    if (frame.active) hold({ member, kind, since, expires_in })
    else forget(`${member}:${kind}`)
  }

  /** 连上时的 `activity_snapshot`：此刻的全部，之前记的一律作废。 */
  function snapshot(members: MemberActivity[]) {
    reset()
    for (const entry of members) hold(entry)
  }

  /** 换了房间，或断了线：上一份名单和这里无关了。 */
  function reset() {
    for (const timer of expiry.values()) clearTimeout(timer)
    expiry.clear()
    entries.value = {}
    lastPing = 0
  }

  /** 房间里除了我以外，此刻在忙的成员。 */
  const others = computed(() => Object.values(entries.value).filter((e) => e.member !== options.me))

  // ---- 我自己在打字 ----
  /** 输入框的内容变了。空了 = 不打了。 */
  function composing(text: string) {
    if (!text.trim()) {
      if (lastPing) options.send({ type: 'typing', active: false })
      lastPing = 0
      return
    }
    const now = Date.now()
    if (now - lastPing < TYPING_PING_MS) return
    lastPing = now
    options.send({ type: 'typing' })
  }

  onScopeDispose(reset)

  return { others, apply, snapshot, reset, composing }
}
