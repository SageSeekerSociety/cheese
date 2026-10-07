// 设备列表里一台设备上次在线的时间：链路断开时记下的最后一次收到消息的时刻，还没断开过时为 null。
import type { MyDevice } from '@/cx_types'

export function lastSeenOf(device: MyDevice): string | null {
  return (device as MyDevice & { last_seen_at?: string | null }).last_seen_at ?? null
}

/** 这台设备的系统（`windows`、`darwin`、`linux`），它的连接程序上次说的；没连上过时为 null。 */
export function systemOf(device: MyDevice): string | null {
  return (device as MyDevice & { system?: string | null }).system ?? null
}
