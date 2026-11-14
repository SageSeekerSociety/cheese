// 一个文件提交项交的几份文件：一份报告和它引用的图片是同一项的一个回答。

/** 一个文件项最多交几份。后端拦的是同一个数（`app/domain/task/services.py` 的
 *  `MAX_FILES_PER_ITEM`）；这里先拦，免得传完一大批才被拒。 */
export const MAX_FILES_PER_ITEM = 20

/** 交上来的这份是不是 Markdown：老师那边按文档画出来，而不是只给一个下载。 */
export function isMarkdownName(name: string): boolean {
  return /\.(md|markdown)$/i.test(name)
}

/** 再选一批加进已选的：同名的换成新选的那份（改了截图重选一次），其余追加在后面。 */
export function mergeFiles(current: readonly File[], added: readonly File[]): File[] {
  const byName = new Map(added.map((file) => [file.name, file]))
  const kept = current.map((file) => byName.get(file.name) ?? file)
  const keptNames = new Set(kept.map((file) => file.name))
  return [...kept, ...added.filter((file) => !keptNames.has(file.name))]
}

/** 自带去处的地址：`http:`、`data:`、`blob:` 这类带协议的，站内的绝对路径，页内锚点。 */
const ADDRESSED = /^([a-z][a-z0-9+.-]*:|\/|#)/i

function decoded(part: string): string {
  try {
    return decodeURIComponent(part)
  } catch {
    return part
  }
}

/** 报告里图片的相对引用（`autograder.png`、`./img/x.png`）在同一项交的文件里指的是哪一份。
 *
 *  浏览器选文件只给文件名、不给目录，所以交上来的几份是平铺的：引用先按整条路径找，找不到
 *  再按最后一段文件名找，都先分大小写、再不分（在 Windows 上写的报告常和文件名差个大小写）。
 *
 *  返回值：带地址的引用原样返回；相对引用找到的是那份文件的地址，找不到是 `null` ——
 *  照写的相对地址去请求，落到的是这个页面自己的路由，只会是一张裂图。 */
export function siblingResolver(files: readonly { name: string; url: string }[]): (src: string) => string | null {
  return (src) => {
    if (!src || ADDRESSED.test(src)) return src
    const parts: string[] = []
    for (const part of src.split(/[?#]/)[0].split('/')) {
      if (part === '' || part === '.') continue
      if (part === '..') parts.pop()
      else parts.push(decoded(part))
    }
    const path = parts.join('/')
    const base = parts[parts.length - 1] ?? ''
    const find = (wanted: string, fold: boolean) =>
      files.find((file) => (fold ? file.name.toLowerCase() === wanted.toLowerCase() : file.name === wanted))
    const hit = find(path, false) ?? find(base, false) ?? find(path, true) ?? find(base, true)
    return hit?.url ?? null
  }
}
