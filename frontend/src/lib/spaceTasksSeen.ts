// 侧栏「全部题目」旁边那个新题目数从哪天算起：这台浏览器上次打开这个空间题目列表的时候。
// 记在本机，换一台设备从头算；读不到存储时当作刚看过，不显示数。
import { storageGet, storageSet } from '@/lib/browserStorage'

const key = (spaceId: number) => `cheesex.spaceTasksSeen.${spaceId}`

/** 上次打开题目列表的时刻（毫秒）；从没打开过是 null。 */
export function tasksSeenAt(spaceId: number): number | null {
  const raw = Number(storageGet(key(spaceId)))
  return Number.isFinite(raw) && raw > 0 ? raw : null
}

export function markTasksSeen(spaceId: number, at: number = Date.now()): void {
  storageSet(key(spaceId), String(at))
}
