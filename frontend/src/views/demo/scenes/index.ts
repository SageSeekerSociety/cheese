// 演示剧本按名字查。名字就是地址 /demo/<名字>，也是文档 fence 里 `embed:` 写的那个。
// docs/site 构建时直接读这几份 JSON，核对步数和每一步的标题和文档里的文字版一致。
import type { Scene } from '../demoScene'

import seats from './seats.json'
import turn from './turn.json'

export const SCENES: Record<string, Scene> = { seats: seats as Scene, turn: turn as Scene }
