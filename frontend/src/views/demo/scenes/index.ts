// 演示剧本按名字查。名字就是地址 /demo/<名字>，也是文档 fence 里 `embed:` 写的那个。
// docs/site 构建时直接读这几份 JSON，核对步数和每一步的标题和文档里的文字版一致。
import type { Scene } from '../demoScene'

import llm from './llm.json'
import machines from './machines.json'
import memory from './memory.json'
import seats from './seats.json'
import turn from './turn.json'

export const SCENES: Record<string, Scene> = {
  turn: turn as Scene,
  seats: seats as Scene,
  memory: memory as Scene,
  llm: llm as Scene,
  machines: machines as Scene,
}
