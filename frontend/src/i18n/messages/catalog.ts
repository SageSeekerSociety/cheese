// 把 `<locale>/index.ts` 那一行 glob 扫到的 JSON 合成 vue-i18n 要的那一团消息。
//
// 这个目录里的**文件路径就是它在命名空间里的键路径**：`account.json` 是命名空间
// `account` 本身，`work/room/doc/index.json` 装 `work.room.doc` 下的键，每一层的
// `index.json` 装这一层自己的散键。所以加一份词表不用改任何登记表——放进这个目录
// 它就在里面了（`docs/i18n.md` 第 2 节）。

/** 一棵消息子树：叶子是字符串，枝干还是子树。 */
export type Messages = { [key: string]: string | Messages }

// 平表命名空间：文件名只表示「这句话是哪个后端模块说的」，不进键名。apiError 的键
// 就是错误码本身（后端 `say("键")`、前端 `apiError.<键>`），所以它在磁盘上分成许多
// 份，读出来仍是一张平表。见 `docs/i18n.md` 第 2 节。
const FLAT: ReadonlySet<string> = new Set(['apiError'])

const isTree = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** 走到 `keys` 那一层，路上缺的枝干顺手建出来。 */
function descend(target: Messages, keys: string[], where: string[]): Messages {
  let node = target
  for (let index = 0; index < keys.length; index += 1) {
    const key = keys[index]
    const child = node[key]
    if (child === undefined) {
      node = node[key] = {}
    } else if (typeof child === 'string') {
      throw new Error(`i18n: ${[...where, key].join('.')} 既是键又是子树`)
    } else {
      node = child
    }
  }
  return node
}

/** 把 `source` 里的叶子接到 `target` 下（`where` 是这些键的完整路径，只用来报错）。
 *
 *  两份文件定义同一个叶子时直接报错，而不是后者悄悄盖掉前者：没有登记表之后，
 *  这是唯一一处还能让两份词表互相覆盖的地方（`docs/i18n.md` 第 2 节的规矩二）。 */
function merge(target: Messages, source: Record<string, unknown>, where: string[], path: string): void {
  for (const [key, value] of Object.entries(source)) {
    const at = [...where, key].join('.')
    const existing = target[key]
    if (typeof value === 'string') {
      if (existing !== undefined) throw new Error(`i18n: ${at} 在两份词表里都定义了（${path}）`)
      target[key] = value
      continue
    }
    if (!isTree(value)) throw new Error(`i18n: ${at} 既不是字符串也不是子树（${path}）`)
    if (existing !== undefined && !isTree(existing)) {
      throw new Error(`i18n: ${at} 在一份词表里是键、在另一份里是子树（${path}）`)
    }
    merge((target[key] ??= {}) as Messages, value, [...where, key], path)
  }
}

/** 每一层的键都按字母序排：两个人各加一个键不再次次落在同一行末尾。 */
function sorted(node: Messages): Messages {
  return Object.fromEntries(
    Object.keys(node)
      .sort()
      .map((key) => {
        const value = node[key]
        return [key, typeof value === 'string' ? value : sorted(value)]
      })
  )
}

/** `<locale>/index.ts` 那一行 glob 的结果 → 一个语言包。`modules` 的键是相对路径，
 *  形如 `./work/room/doc/index.json`。 */
export function buildCatalog(modules: Record<string, unknown>): Messages {
  const catalog: Messages = {}
  for (const [modulePath, value] of Object.entries(modules)) {
    if (!isTree(value)) throw new Error(`i18n: ${modulePath} 不是一个 JSON 对象`)
    const segments = modulePath
      .replace(/^\.\//, '')
      .replace(/\.json$/, '')
      .split('/')
    const namespace = segments.shift() as string
    // 结尾的 `index` 说的是「这一层」，不是某个叫 index 的键。（它于是成了保留的树名：
    // `work/index.json` 永远是 work 自己那一层的键。）
    if (segments[segments.length - 1] === 'index') segments.pop()
    const keys = FLAT.has(namespace) ? [] : segments
    const root = (catalog[namespace] ??= {}) as Messages
    merge(descend(root, keys, [namespace]), value, [namespace, ...keys], modulePath)
  }
  return sorted(catalog)
}
