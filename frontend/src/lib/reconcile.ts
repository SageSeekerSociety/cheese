// 刷新回来的数据和屏幕上那一份按内容合并：没变的部分沿用旧对象，只有真变了的
// 地方换成新的。
//
// 界面的「不闪」有两半。一半是刷新期间别把旧的清掉（query 缓存、各处
// 「换对象才清空」的写法管这一半）；另一半是新的一份到了之后，别把整棵树换成一批
// 内容相同、身份不同的新对象：列表的 key 没变，Vue 不会重建行，可每一个以对象为
// 输入的 computed、watch、子组件的 props 都会当成「变了」再跑一遍，有入场动画的
// 地方还会再演一次。这里管的是后一半。
//
// 数组里的对象有 `id` 时按 id 配对（行的顺序可以变，没变的行仍是原来那个对象）；
// 没有 id 的按下标配对。

type Plain = Record<string, unknown>

function isPlainObject(value: unknown): value is Plain {
  if (value === null || typeof value !== 'object') return false
  const proto = Object.getPrototypeOf(value) as unknown
  return proto === Object.prototype || proto === null
}

function idOf(value: unknown): unknown {
  return isPlainObject(value) ? value.id : undefined
}

function reconcileArray(prev: unknown[], next: unknown[]): unknown[] {
  const keyed =
    next.length > 0 &&
    next.every((item) => {
      const id = idOf(item)
      return typeof id === 'string' || typeof id === 'number'
    })
  const byId = keyed ? new Map(prev.map((item) => [idOf(item), item])) : null
  let same = prev.length === next.length
  const out = next.map((item, at) => {
    const before = byId ? byId.get(idOf(item)) : prev[at]
    const merged = before === undefined ? item : reconcile(before, item)
    if (merged !== prev[at]) same = false
    return merged
  })
  return same ? prev : out
}

function reconcileObject(prev: Plain, next: Plain): Plain {
  const keys = Object.keys(next)
  let same = keys.length === Object.keys(prev).length
  const out: Plain = {}
  for (const key of keys) {
    const merged = key in prev ? reconcile(prev[key], next[key]) : next[key]
    if (merged !== prev[key] || !(key in prev)) same = false
    out[key] = merged
  }
  return same ? prev : out
}

/**
 * 把 `next` 合到 `prev` 上：内容和 `prev` 相同的子树原样返回 `prev` 里的那个对象，
 * 整体都没变就返回 `prev` 本身。只深入普通对象和数组；别的值（Date、Map、类的
 * 实例）按 `===` 比较。
 */
export function reconcile<T>(prev: T, next: T): T
export function reconcile(prev: unknown, next: unknown): unknown {
  if (Object.is(prev, next)) return prev
  if (Array.isArray(prev) && Array.isArray(next)) return reconcileArray(prev, next)
  if (isPlainObject(prev) && isPlainObject(next)) return reconcileObject(prev, next)
  return next
}
