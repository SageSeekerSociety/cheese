import type { MyDevice } from '@/cx_types'

/** 我该把活儿派给哪台机器。没有「这就是我这台机器」的概念，只能猜一台：在线的优先，
 *  否则第一台。队友技能面板和全局技能市场页用的是同一套猜法（两处各写一份会漂）。
 *
 *  ponytail: 天花板是猜错机器；升级路径 = 记住上次选的那台 `device_id`。 */
export function pickMyDevice(devices: MyDevice[]): MyDevice | undefined {
  return devices.find((d) => d.online) ?? devices[0]
}
