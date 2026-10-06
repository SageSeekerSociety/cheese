// 全应用同一时间只留一份菜单。
//
// 右键弹出来的那一份在鼠标那一点上、不挂在哪一颗按钮底下，而右键不产生 click：浮层
// 自己那套「点别处就关」叫不动它。没有这一处登记，旧的那一份就一直留在原地——右键点
// 第二处，新的弹出来了，上一个还在，点几次垒几份（2026-10-06 lz123y 报的）。
//
// 谁开菜单谁登记；「关」是把它自己的 v-model 置 false，菜单怎么开、归谁管都是它自己的
// 事，这里只负责叫它。
let current: (() => void) | null = null

/** 开了一份菜单。`atPointer` 是右键那一份：它开的时候把当时开着的那一份收掉。 */
export function menuOpened(close: () => void, atPointer: boolean) {
  const previous = current
  current = close
  if (atPointer && previous && previous !== close) previous()
}

/** 这一份自己关掉了。 */
export function menuClosed(close: () => void) {
  if (current === close) current = null
}
