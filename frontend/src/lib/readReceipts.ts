// 已读：一条消息谁读过、谁还没读。
//
// 后端按「读的人」给读过的几段编号（`api/messageReads`）；一条消息读没读过，拿它
// 的编号（`Block.seq`）去对。这里只做这几件不碰网络的事：合并几段编号、对一条消息
// 分出读过和没读的人、把屏幕上看过的编号收成几段好报上去。

/** 编号 `[first, last]`，两头都算。 */
export type Stretch = [number, number]

/** 消息气泡旁那一圈里的一个人：人或 AI 队友，带界面上叫他的名字和头像。 */
export interface ReceiptPerson {
  handle: string
  name: string
  avatar: string | null
  isAgent: boolean
}

/** 一条消息的已读情况：读过的、没读的，都按名册的顺序。 */
export interface Receipt {
  read: ReceiptPerson[]
  unread: ReceiptPerson[]
}

/** 几段编号合成最少的几段：重叠的、首尾相接的并成一段。 */
export function mergeStretches(stretches: readonly Stretch[]): Stretch[] {
  const out: Stretch[] = []
  for (const [first, last] of [...stretches].sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    const tail = out.at(-1)
    if (tail && first <= tail[1] + 1) tail[1] = Math.max(tail[1], last)
    else out.push([first, last])
  }
  return out
}

export function covers(stretches: readonly Stretch[] | undefined, seq: number): boolean {
  return !!stretches?.some(([first, last]) => first <= seq && seq <= last)
}

/** 一堆零散的编号收成几段连着的，报给后端用。 */
export function stretchesOf(seqs: Iterable<number>): Stretch[] {
  return mergeStretches([...seqs].map((s): Stretch => [s, s]))
}

/**
 * 一条消息的已读情况：这个对话里能读到它的人（`people`，名册的顺序）里，除了说这句
 * 话的人自己，谁的读过的那几段盖住了它的编号。名册上一个人都不剩时没有这一圈。
 */
export function receiptOf(
  seq: number,
  author: string,
  people: readonly ReceiptPerson[],
  reads: ReadonlyMap<string, readonly Stretch[]>
): Receipt | null {
  const others = people.filter((p) => p.handle !== author)
  if (!others.length) return null
  const read: ReceiptPerson[] = []
  const unread: ReceiptPerson[] = []
  for (const person of others) (covers(reads.get(person.handle), seq) ? read : unread).push(person)
  return { read, unread }
}
