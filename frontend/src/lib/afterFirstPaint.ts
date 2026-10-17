// 首帧画完之后：角标、装饰类的一批（待办数、未读点、反馈计数、版本号、默认头像）
// 排到这一刻再发，不跟「把内容画出来」抢同一条网络、同一个主线程。
//
// 用的还是 `lib/idle.ts` 的 `whenIdle`（仓储里 `ProjectShell` 延后 2.5 秒预热、
// `TopicMembers.vue` 都是先例），这里只把「哪一刻算首帧之后」收在一处，好让这几处
// 保持同一个口径。
//
// 判据是**这一次跳转落地之后再让出一帧**：`vue-router` 是等目标那一页的懒加载 chunk
// 下完才确认跳转的，确认之后页面组件才挂上、它自己要的请求才发出去，`whenIdle` 排在
// 渲染这一拍之后，所以这批一定在页面自己的数据后面。切页会把还没发出去的那批取消、
// 按新页面重排一次——切得快的时候，上一页那几个角标压根不发。
//
// 一旦过了首帧就一直算过了：这几个请求整个会话只问第一遍，之后由各自的推送或人在
// 页面上的动作触发。翻回 false 会让项目订阅重连、角标每次切页重读一遍。
import type { Router } from 'vue-router'

import { readonly, ref } from 'vue'

import { whenIdle } from './idle'

const done = ref(false)
let stopIdle: (() => void) | null = null

/** 这一次打开的首帧画完了没有。角标、装饰类读它。 */
export const firstPaintDone = readonly(done)

/** 跟着这台 router 的跳转排一次；装一次就够（`main.ts` 里装）。返回的是摘下来的手。 */
export function armAfterFirstPaint(router: Router): () => void {
  const stop = router.afterEach(() => {
    if (done.value) return
    stopIdle?.()
    stopIdle = whenIdle(() => {
      stopIdle = null
      done.value = true
    })
  })
  return () => {
    stop()
    stopIdle?.()
    stopIdle = null
    done.value = false
  }
}
