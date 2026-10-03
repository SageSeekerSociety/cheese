// 命令面板怎么判断一条东西和输入的字对不对得上、对得有多好。
//
// 三种对法，好的排前面：名字以输入开头 > 名字里有这段字 > 拼音首字母对得上（「dlyg」
// 找到「登录页改成深色」）。别名（keywords）和名字一样对。输入里有空格就拆成几个词，
// 每个词都要对上。

// ---- 拼音首字母 --------------------------------------------------------------
//
// 不带拼音字典：浏览器自带的中文排序规则就是按拼音排的，找出一个字落在哪两个声母
// 的「第一个字」之间，就知道它的首字母。一个字只取得到一个读音，常用的多音字在下面
// 的表里补上别的读音，两个都认。浏览器不支持中文排序时整个不算，宁可找不到，也不给
// 错的首字母。

const BOUNDS: [string, string][] = [
  ['a', '阿'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['b', '八'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['c', '嚓'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['d', '哒'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['e', '妸'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['f', '发'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['g', '旮'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['h', '哈'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['j', '丌'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['k', '咔'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['l', '垃'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['m', '妈'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['n', '拏'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['o', '噢'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['p', '妑'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['q', '七'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['r', '呥'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['s', '仨'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['t', '他'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['w', '屲'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['x', '夕'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['y', '丫'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
  ['z', '帀'], // i18n-data: 拼音排序的声母锚点，只拿来比较，不显示
]

// 常用多音字的另一个声母。排序规则只给一个读音，「重命名」会被当成 z 开头。
const OTHER_READINGS: Record<string, string> = {
  重: 'c',
  长: 'z',
  行: 'h',
  乐: 'y',
  还: 'h',
  调: 'dt',
  传: 'z',
  单: 's',
  会: 'k',
  解: 'x',
  便: 'p',
  藏: 'z',
  参: 's',
  朝: 'c',
  曾: 'z',
}

let collator: Intl.Collator | null | undefined

function zhCollator(): Intl.Collator | null {
  if (collator !== undefined) return collator
  try {
    const candidate = new Intl.Collator('zh-Hans-CN')
    collator = candidate.resolvedOptions().locale.startsWith('zh') ? candidate : null
  } catch {
    collator = null
  }
  return collator
}

const initialsCache = new Map<string, string[]>()

/**
 * 一段字的拼音首字母，一个位置一组候选（多音字不止一个）。字母和数字原样保留，
 * 其余符号跳过。
 */
export function initialsOf(text: string): string[] {
  const cached = initialsCache.get(text)
  if (cached) return cached
  const col = zhCollator()
  const out: string[] = []
  for (const ch of text) {
    if (/[a-z0-9]/i.test(ch)) {
      out.push(ch.toLowerCase())
      continue
    }
    if (!col || !/\p{Script=Han}/u.test(ch)) continue
    let letter = ''
    for (const [initial, first] of BOUNDS) {
      if (col.compare(ch, first) >= 0) letter = initial
      else break
    }
    if (!letter) continue
    out.push(letter + (OTHER_READINGS[ch] ?? ''))
  }
  initialsCache.set(text, out)
  return out
}

/** 输入的字母能不能对上首字母里连续的一段；开头对上更好。 */
function initialsAt(query: string, initials: string[]): number {
  if (!/^[a-z0-9]+$/.test(query) || query.length > initials.length) return -1
  for (let start = 0; start + query.length <= initials.length; start += 1) {
    let ok = true
    for (let i = 0; i < query.length; i += 1) {
      if (!initials[start + i].includes(query[i])) {
        ok = false
        break
      }
    }
    if (ok) return start
  }
  return -1
}

// ---- 打分 --------------------------------------------------------------------

export interface MatchResult {
  score: number
  /** 名字里对上的那一段（高亮用）；首字母对上时没有。 */
  range: [number, number] | null
}

function scoreWord(word: string, title: string, keywords: string[]): MatchResult | null {
  const lower = title.toLowerCase()
  const at = lower.indexOf(word)
  if (at === 0) return { score: 100, range: [0, word.length] }
  if (at > 0) return { score: 80 - Math.min(at, 20), range: [at, at + word.length] }
  if (keywords.some((k) => k.toLowerCase().includes(word))) return { score: 60, range: null }
  const initialAt = initialsAt(word, initialsOf(title))
  if (initialAt === 0) return { score: 70, range: null }
  if (initialAt > 0) return { score: 50, range: null }
  return null
}

/** 输入对不上就是 null。空输入人人都对得上，分数一样。 */
export function match(query: string, title: string, keywords: string[] = []): MatchResult | null {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return { score: 1, range: null }
  let score = Infinity
  let range: [number, number] | null = null
  for (const word of words) {
    const hit = scoreWord(word, title, keywords)
    if (!hit) return null
    score = Math.min(score, hit.score)
    range ??= hit.range
  }
  return { score, range }
}
