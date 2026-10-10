// 一台机主接入的电脑能不能给会话沙箱，是它连上时自己答的（后端 `device.sandbox`）。
import type { MyDevice } from '@/cx_types'

/** What the machine last said; its owner's only. `reason` is the machine's own words. */
export interface DeviceSandbox {
  isolates: boolean
  reason: string | null
  checked_at: string
}

/** 「我的设备」里这台电脑说过的话；null 是还没被问过。 */
export function sandboxOf(device: MyDevice): DeviceSandbox | null {
  return (device as MyDevice & { sandbox?: DeviceSandbox | null }).sandbox ?? null
}
