// 启动骨架（index.html 里那段启动脚本）下次冷打开时要照着画的那几样，应用里只有这里写。
// 键和 index.html 里读的那一个是同一个，改一处要连另一处一起改。
import { storageSet } from '@/lib/browserStorage'

const RAIL_COUNT_KEY = 'cheesex.railCount'

/** rail 上有几个项目格。 */
export function rememberRailCount(count: number): void {
  storageSet(RAIL_COUNT_KEY, String(count))
}
