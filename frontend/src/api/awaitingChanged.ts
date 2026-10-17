// 「待我处理」可能刚变了：人做成了一件会了结（或放回）一件待办的事——拍板、标为已读、
// 回答提问、采纳或退回改动、开始或关闭任务。
//
// 做这些事的端点各自在自己的 api 模块里用 `changesAwaiting` 包一下，角标
// （`useAwaitingCount`）只听这一处。所以不管人是在待办页、房间里还是动态里做的，
// 调用方都不用记着去叫角标重读；新加一个会了结待办的端点，也只在它的定义上包一下。
// 写失败不广播：什么都没变。

const listeners = new Set<() => void>()

/** 有一件待办可能刚了结时叫 `listener`；返回取消订阅的函数。 */
export function onAwaitingChanged(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 这个写成功之后告诉角标重读；结果原样交回调用方。 */
export function changesAwaiting<T>(write: Promise<T>): Promise<T> {
  return write.then((result) => {
    for (const listener of listeners) listener()
    return result
  })
}
