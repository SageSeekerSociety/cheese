// 消息区里点到的 chip 和头像怎么落到父级的事件上。@mention 的 chip 是 v-html
// 出来的，没法在模板上挂 handler，只能在整个消息区上代理一次点击，再按 `data-*`
// 说清点到的是谁。一条消息的头像和名字（.im-person）走同一条路——它代表的是那条
// 消息的作者。
//
// 这里只回答「点到什么 → 报给谁」：开话题、开卡片、开文件都是父级的事。
import type { Ref } from 'vue'
import type { Block } from '../cx_types'
import type { ChatPanelEmit } from './chatPanelContract'

export function useChatMessageClicks(deps: {
  /** 此刻对着哪条消息开着表情选择器；点到别处就收起它（它内部那几下不归这儿管）。 */
  reactionPickerFor: Ref<string | null>
  /** 触屏上轻点一下别的地方是「亮出这一行的时间」，不是打开卡片。 */
  touchOnly: Ref<boolean>
  toggleTime: (target: HTMLElement) => void
  /** 消息行本身，用来数出 chip 所在那行挂着哪件活（`<#id>` 可能是话题，也可能是活）。 */
  rows: () => { block: Block }[]
  /** 这间里已派出的活，用来把 `<#id>` 分成「开卡片」还是「开话题」。 */
  roomTasks: () => { id: string }[]
  emit: ChatPanelEmit
}): (e: MouseEvent) => void {
  return function onMessagesClick(e: MouseEvent) {
    const target = e.target as HTMLElement | null
    // Click-away closes the emoji picker (clicks inside it are handled there).
    if (deps.reactionPickerFor.value && !target?.closest('.rx-picker, .rx-toggle')) {
      deps.reactionPickerFor.value = null
    }
    if (deps.touchOnly.value && target) deps.toggleTime(target)
    const el = target?.closest('.mention, .im-person') as HTMLElement | null
    if (!el) return
    // 在动的那个头像：它此刻在干的事在「现场」，点它就去那里。
    if (el.dataset.site !== undefined) deps.emit('open-resource', 'site')
    else if (el.dataset.handle) deps.emit('mention-click', el.dataset.handle)
    else if (el.dataset.topic) {
      const id = el.dataset.topic
      if (deps.roomTasks().some((task) => task.id === id)) deps.emit('open-card', id)
      else deps.emit('open-topic', id)
    } else if (el.dataset.file) {
      deps.emit('open-file', el.dataset.file)
    }
  }
}
