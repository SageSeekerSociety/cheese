// 演示剧本按名字查。名字就是文件名，也是地址 /demo/<名字> 和文档 fence 里 `embed:`
// 写的那个。加一个演示只要在这个目录放一份 JSON，不用改这里。
// docs/site 构建时直接读这几份 JSON，核对步数和每一步的标题和文档里的文字版一致。
import type { Scene } from '../demoScene'

const files = import.meta.glob<Scene>('./*.json', { eager: true, import: 'default' })

export const SCENES: Record<string, Scene> = Object.fromEntries(
  Object.entries(files).map(([path, scene]) => [path.replace(/^\.\/(.*)\.json$/, '$1'), scene])
)
