/**
 * 这台浏览器上的键值存储：全仓库唯一许直接碰 `localStorage` / `sessionStorage` 的地方。
 *
 * 为什么要有它：以前每个文件各写一份 try/catch + `JSON.parse` + 形状检查，写法各不相同，
 * 而且大半干脆没有 —— Safari 隐私模式、配额写满、站点数据被禁时 `setItem` 会抛，一次写
 * 失败就把整条流程带下去（登录、写评论的重试记录、工作区 store 都在这一串上）。这里把
 * 「存储随时可能抛」收在一处：读不出来当作没存过，写不进去就让这一次会话算数。调用者
 * 不必再自己 try/catch，也不该再自己取那两个全局。
 *
 * 两条判据刻意定死，别按老习惯改回去：
 * 1. **解析不出来的那条记录删掉**。它不会变回可解析，留着等于每次进来都在同一个地方
 *    静默失败；删的只有这一条读不动的记录。
 * 2. **形状不对（JSON 能解析、类型不对）不动它**，交给调用者判。删数据要有理由，而
 *    「这一次读到的形状不是我要的」不是理由。
 *
 * 闸门：`pnpm run lint:storage`（eslint.browser-storage.config.mjs + browser-storage-baseline.json）。
 * 新代码不许绕过这里；baseline 里是存量，只许少 —— 迁完一处就删掉那一行。
 */

/** 存在哪一份里。local 跨会话留着，session 关掉标签页就没。 */
export type StorageArea = 'local' | 'session'

/**
 * 这一份存储对象，或者 null —— 没有 window（SSR、node）时、以及站点数据被禁时都拿不到。
 * 取这两个全局本身就可能抛（有些浏览器把访问器本身设成会抛的），所以连取带判都在里面。
 */
function store(area: StorageArea): Storage | null {
  try {
    const found = area === 'session' ? globalThis.sessionStorage : globalThis.localStorage
    return found ?? null
  } catch {
    return null
  }
}

/** 读一个字符串；没存过、读不到，都是 null。 */
export function storageGet(key: string, area: StorageArea = 'local'): string | null {
  try {
    return store(area)?.getItem(key) ?? null
  } catch {
    return null
  }
}

/** 写一个字符串。写不进去（隐私模式、配额满）返回 false，不抛 —— 调用者当这一次没记住即可。 */
export function storageSet(key: string, value: string, area: StorageArea = 'local'): boolean {
  try {
    const target = store(area)
    if (!target) return false
    target.setItem(key, value)
    return true
  } catch {
    return false
  }
}

/** 删一条。删不掉也就算了：下一次写会盖掉它。 */
export function storageRemove(key: string, area: StorageArea = 'local'): void {
  try {
    store(area)?.removeItem(key)
  } catch {
    // 删不动（站点数据被禁）：这条记录本来也读不出来。
  }
}

/**
 * 读一条 JSON。没存过、解析不出来都是 null；解析不出来的那条记录顺手删掉（判据 1）。
 * 返回的值只是「存进去什么就是什么」，形状由调用者自己判（判据 2）。
 */
export function storageGetJson<T = unknown>(key: string, area: StorageArea = 'local'): T | null {
  const raw = storageGet(key, area)
  if (raw === null) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    storageRemove(key, area)
    return null
  }
}

/**
 * 把值写成 JSON。序列化不了（循环引用）和写不进去一样返回 false。
 *
 * `maxChars` 按序列化后的长度算，超了就当作不写 —— 给「一条记录里可能粘进一整个
 * 文件，写满了会挤掉别人」的那种调用者用，它自己那层上限是多少、由它自己传。
 */
export function storageSetJson(
  key: string,
  value: unknown,
  area: StorageArea = 'local',
  maxChars: number = Number.POSITIVE_INFINITY
): boolean {
  let raw: string | undefined
  try {
    raw = JSON.stringify(value)
  } catch {
    return false
  }
  if (raw === undefined || raw.length > maxChars) return false
  return storageSet(key, raw, area)
}

/**
 * 这一份存储里所有以 `prefix` 开头的键，顺序不保证；存储不可用时是空数组。
 * 枚举和读、写一样，都是那两个全局的能力，所以也收在这里 —— 按前缀清理或淘汰
 * 「一条记录一个键」的调用者（话题一条、草稿一条）不必自己去数 `store.length`。
 */
export function storageKeys(prefix: string, area: StorageArea = 'local'): string[] {
  const target = store(area)
  if (!target) return []
  const keys: string[] = []
  try {
    for (let i = 0; i < target.length; i++) {
      const key = target.key(i)
      if (key !== null && key.startsWith(prefix)) keys.push(key)
    }
  } catch {
    // 遍历到一半被拒（站点数据被禁）：已经拿到的就是全部。
  }
  return keys
}

/** 读一份字符串数组（存进去的是 JSON 数组）。不是数组、里面混了别的东西，都按过滤后的结果算。 */
export function storageGetStrings(key: string, area: StorageArea = 'local'): string[] {
  const parsed = storageGetJson<unknown>(key, area)
  return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : []
}
