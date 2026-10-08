// 房间通道的替身：每个房间直接拿测试里换上的那个假 WebSocket（地址里带房间 id，
// 用例按它认是哪一间）。用例在它身上手动开、推帧、断开，和以前一房一条时一样。
// 一页一条连接、按房间分帧那一层，`lib/roomLink.spec.ts` 自己测。
//
//   vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
import type { RoomChannel } from '@/lib/roomLink'

export function openRoomChannel(topic: string): RoomChannel {
  return new WebSocket(`ws://test/chat/${topic}`) as unknown as RoomChannel
}

export function resetRoomLink(): void {}
