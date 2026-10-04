// 长回复折叠的量高，所有消息行共用一个批次。
//
// 每一行原来各量各的：挂上来就 `getComputedStyle` + `scrollHeight`，再写回自己的
// 状态。首屏挂几十行时，这是「读一次布局、写一次、再读一次」交替几十遍，每一次读都
// 逼浏览器把上一次写引起的布局重算一遍（layout thrashing）。另外每一行一个
// ResizeObserver，几百行就是几百个观察者。
//
// 这里把它们收成一批：同一帧里要量的行先**全部读完**，再**全部写**；观察者全页只有
// 一个，按元素分发。判据仍在 lib/chatFold。

type Read<R> = () => R
type Write<R> = (value: R) => void

interface Task {
  read: Read<unknown>
  write: Write<unknown>
}

// 同一个 owner 一帧里只量一次：挂载、nextTick、尺寸变化可能在同一帧里都来要。
const queue = new Map<object, Task>()
let frame = 0

function flush(): void {
  frame = 0
  const tasks = [...queue.values()]
  queue.clear()
  const values = tasks.map((task) => task.read())
  tasks.forEach((task, i) => task.write(values[i]))
}

function schedule(): void {
  if (frame) return
  frame =
    typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(flush)
      : (setTimeout(flush, 0) as unknown as number)
}

/** 把一次「读布局 → 写状态」排进下一帧的批次。同一个 `owner` 只保留最后一次。 */
export function queueMeasure<R>(owner: object, read: Read<R>, write: Write<R>): void {
  queue.set(owner, { read, write: write as Write<unknown> })
  schedule()
}

/** 这一行卸载了：还没量的那次不要了。 */
export function cancelMeasure(owner: object): void {
  queue.delete(owner)
}

const callbacks = new WeakMap<Element, () => void>()
let observer: ResizeObserver | null = null

/** 全页共用的那一个 ResizeObserver。返回取消观察的函数。 */
export function observeSize(el: Element, onResize: () => void): () => void {
  if (typeof ResizeObserver === 'undefined') return () => {}
  observer ??= new ResizeObserver((entries) => {
    for (const entry of entries) callbacks.get(entry.target)?.()
  })
  callbacks.set(el, onResize)
  observer.observe(el)
  return () => {
    callbacks.delete(el)
    observer?.unobserve(el)
  }
}
