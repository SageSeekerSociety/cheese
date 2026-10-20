// 「我有哪些空间」可能刚变了：建了一个、重新提交了申请、撤回或删掉了一个、用邀请码
// 加入了一个、退出了一个。
//
// 首页侧栏的空间那一组（`useTeamNav`）只听这一处，所以在哪一页做的这些事都不用记着
// 去叫侧栏重读。写失败不广播：什么都没变。和 `awaitingChanged.ts` 同一个形状。

const listeners = new Set<() => void>()

/** 空间名单可能刚变时叫 `listener`；返回取消订阅的函数。 */
export function onSpacesChanged(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 这个写成功之后告诉侧栏重读；结果原样交回调用方。 */
export function changesSpaces<T>(write: Promise<T>): Promise<T> {
  return write.then((result) => {
    for (const listener of listeners) listener()
    return result
  })
}
