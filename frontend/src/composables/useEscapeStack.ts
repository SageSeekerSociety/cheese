import { onScopeDispose, type Ref, watch } from 'vue'

// 一层「按 Esc 能关掉」的浮层。平板横放（960–1180）那一档里，二级侧栏浮层和话题的
// 工作面板浮层可以同时开着，从前是两个组件各装一个 window keydown：一下 Esc 两层一起
// 关，人分不清刚收起来的是哪一层，焦点也不知道回到了哪颗开关。
//
// 改成一层全局的栈：谁打开谁入栈，Esc 只打发栈顶那层（也就是最后打开的那层），第二
// 下 Esc 才轮到压在下面那层——和盖在屏幕上的顺序一致。全栈只听一个 window keydown。
//
// 一层的「关」由调用方给全：收起它，并把焦点还给打开它的那颗开关。这样两层各自回到
// 各自的那颗，栈本身不用认识焦点在哪。

interface EscapeLayer {
  close: () => void
}

// 打开顺序即栈序：后打开的压在上面。空栈时不留 window 监听。
const stack: EscapeLayer[] = []
let listening = false

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  // 这一下 Esc 已经被更靠里的东西吃掉了（输入框里 @ 菜单或回答面板先收起了自己，
  // `preventDefault` 过）：它不算浮层栈的，别连浮层一起关。window 的监听排在事件冒泡
  // 的最后，里面的 @keydown 先跑，所以到这儿 defaultPrevented 已经写好了。
  if (event.defaultPrevented) return
  const top = stack[stack.length - 1]
  if (!top) return
  event.preventDefault()
  top.close()
}

function listen() {
  if (listening) return
  window.addEventListener('keydown', onKeydown)
  listening = true
}

function unlisten() {
  if (!listening) return
  window.removeEventListener('keydown', onKeydown)
  listening = false
}

/**
 * 把一层浮层接进 Esc 栈。`active` 为真时它入栈（此刻能按 Esc 关掉），变假就出栈；
 * 组件卸载时一并退栈。`close` 是关掉它的那一下。
 */
export function useEscapeLayer(active: Readonly<Ref<boolean>>, close: () => void): void {
  let layer: EscapeLayer | null = null

  function push() {
    if (layer) return
    layer = { close }
    stack.push(layer)
    listen()
  }

  function pop() {
    if (!layer) return
    const at = stack.indexOf(layer)
    if (at >= 0) stack.splice(at, 1)
    layer = null
    if (stack.length === 0) unlisten()
  }

  watch(active, (on) => (on ? push() : pop()), { immediate: true })
  onScopeDispose(pop)
}
