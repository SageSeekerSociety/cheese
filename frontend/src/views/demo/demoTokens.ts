// 设计 token 页要的那点数据：产品声明了哪些 token、每条在两档主题下各是多少。
//
// 一条硬规矩：**这一页不许有自己抄下来的 token 表**。抄一份就会漂——`style.css` 把
// `--dur-base` 从 220ms 改成 200ms，抄的那份还写着 220，而这一页存在的全部理由就是
// 「改 token 之前先在这里看后果」。所以值一律从 `document.styleSheets` 里现读，读不到
// 就如实说读不到，不猜一个默认值。
//
// 读的是选择器**正好**是 `:root` 和 `:root[data-theme='dark']` 那两条规则里的声明，
// 也就是设计 token 的定义处：组件自己临时声明的自定义属性不是设计 token，`@media`
// 里那些只在某个宽度下成立的值也不是（那种垫在下面，只在没有定义处时才露出来）。
// `demoMotion.ts` 读的是同一批声明（那边的 `--dur-*` 名字），差别是它改值、这里显值。

/** 只用到一条规则身上这几样，好让测试摆一份假的样式表进来。 */
export interface TokenDecls {
  readonly length: number
  item(index: number): string | null
  getPropertyValue(name: string): string
}

export interface StyleRule {
  /** 只为了和真的 `CSSRule` 有共同字段（两边都是弱类型时 TypeScript 才肯收）。 */
  cssText?: string
  selectorText?: string | null
  style?: TokenDecls | null
  cssRules?: ArrayLike<StyleRule> | null
}

export interface StyleSheetLike {
  cssRules?: ArrayLike<StyleRule> | null
}

export type StyleSheetsLike = ArrayLike<StyleSheetLike>

export interface TokenScopes {
  /** `:root` 里声明的：浅色那一档，也是每条 token 的出处。 */
  light: Map<string, string>
  /** `:root[data-theme='dark']` 里**重新声明过**的。没在这个 map 里的，深色下就是浅色那份值。 */
  dark: Map<string, string>
  /**
   * 在 `@media` 之类条件下**改过定义处**的值，浅色档。
   *
   * 上面两档已经把这一层垫在定义处下面了（所以 `light` 里是桌面那一版）。这里另留一
   * 份，是为了让页面能标出「这个值是哪个条件下的」——产品的页宽就是：920px 是定义处，
   * 959.98px 以下那条 `:root` 把它改成 720px。
   */
  conditional: Map<string, string>
}

const CUSTOM_PROPERTY = /^--/
/** 定义处的两条选择器。只有**整条**对得上才算，见 `scopeOf`。 */
const LIGHT_SELECTOR = /^:root$/i
const DARK_SELECTOR = /^:root\[\s*data-theme\s*=\s*['"]?dark['"]?\s*\]$/i

/** 一条 token 落在哪一档。 */
type Scope = 'light' | 'dark'

/**
 * 这条规则定义的是哪一档；不是 token 的定义处就返回 null。
 *
 * 判据必须整条对得上，不能用「选择器里出现 `:root` 就算」：产品里有
 * `:root[data-theme='dark'] .v-menu > .v-overlay__content { --fill: var(--fill-2) }`
 * 这种给浮层打补丁的规则，它改的是组件局部。认了它，深色格上的 `--fill` 就会显示成
 * `--fill-2` 的值，而这一页存在的全部理由就是「改 token 之前先在这里看一眼」。
 */
function scopeOf(selectorText: string | null | undefined): Scope | null {
  const selector = (selectorText ?? '').trim()
  if (LIGHT_SELECTOR.test(selector)) return 'light'
  if (DARK_SELECTOR.test(selector)) return 'dark'
  return null
}

/**
 * 某一档主题下，一条 token 最后算出什么值。
 *
 * 值可以是另一条 token（深色块的 `--code-bg: var(--fill-2)` 就是），所以要把引用解
 * 开——直接拿 `var(--fill-2)` 去刷色块，刷出来的是**当前**主题的颜色，不是深色档的。
 * `fallback` 是另一档：深色块只写它改过的那些，没写的沿用浅色。
 */
export function resolveIn(
  value: string,
  scope: Map<string, string>,
  fallback: Map<string, string> = new Map()
): string {
  let out = value
  // 八轮足够：这个仓库里的引用链最多两层。剩下的原样留着，好过死循环。
  for (let round = 0; round < 8; round += 1) {
    const next = out.replace(
      /var\(\s*(--[\w-]+)\s*\)/g,
      (whole, ref: string) => scope.get(ref) ?? fallback.get(ref) ?? whole
    )
    if (next === out) break
    out = next
  }
  return out.trim()
}

function collect(
  rule: StyleRule,
  into: (scope: Scope, decls: TokenDecls, inCondition: boolean) => void,
  inCondition: boolean
): void {
  const scope = scopeOf(rule.selectorText)
  if (scope && rule.style) into(scope, rule.style, inCondition)
  if (!rule.cssRules) return
  for (let i = 0; i < rule.cssRules.length; i += 1) {
    const inner = rule.cssRules[i]
    // 套在别人里头的规则就是**有条件**的：`@media`、`@supports`、`@layer` 里那一条
    // `:root` 只在那个条件下成立，不是这一条 token 的定义处。
    if (inner) collect(inner, into, true)
  }
}

/** 从几份样式表里读出两档 token。读不到的样式表（跨域、坏规则）跳过，不影响其余。 */
export function readTokenScopes(sheets: StyleSheetsLike): TokenScopes {
  /** 顶层那一条 `:root` / `:root[data-theme='dark']` 里声明的：定义处。 */
  const plain: Record<Scope, Map<string, string>> = { light: new Map(), dark: new Map() }
  /** 媒体查询之类条件下声明的同名 token：只在那个条件下成立，搁在下面垫着。 */
  const guarded: Record<Scope, Map<string, string>> = { light: new Map(), dark: new Map() }
  const take = (scope: Scope, decls: TokenDecls, inCondition: boolean): void => {
    const into = (inCondition ? guarded : plain)[scope]
    for (let i = 0; i < decls.length; i += 1) {
      const name = decls.item(i)
      if (!name || !CUSTOM_PROPERTY.test(name)) continue
      const value = decls.getPropertyValue(name).trim()
      if (value) into.set(name, value)
    }
  }
  for (let i = 0; i < sheets.length; i += 1) {
    try {
      // 取 `cssRules` 这一步也要在 try 里：别的源的样式表**取这个属性本身就抛**，
      // 放在外面会让一张读不了的样式表带走后面所有的。
      const rules = sheets[i]?.cssRules
      if (!rules) continue
      for (let j = 0; j < rules.length; j += 1) {
        const rule = rules[j]
        if (rule) collect(rule, take, false)
      }
    } catch {
      // 读不到就少几行，不影响读得到的那些。
    }
  }
  // 有条件的垫在定义处下面：`@media (max-width: 959.98px)` 里那条 `--page-w: 720px`
  // 是这一条 token 在窄屏上的值，不是它的定义处——盖掉 920px，这一页就会把产品在
  // 桌面上的页宽说错。只有产品**只在**那个条件下声明过的 token 才从这一层露出来。
  return {
    light: new Map([...guarded.light, ...plain.light]),
    dark: new Map([...guarded.dark, ...plain.dark]),
    // 只留**改过定义处**的那些：它确实在某个条件下生效，页面上标得出来。只在条件里
    // 出现的（定义处没有）不进这一层——它已经垫进 `light` 了，标「另有一个值」不成立。
    conditional: new Map(
      [...guarded.light].filter(([name, value]) => plain.light.has(name) && plain.light.get(name) !== value)
    ),
  }
}

/** 文档里这几份样式表读出来的 token。`document` 不在时（测试、构建期）返回空档。 */
export function documentTokenScopes(): TokenScopes {
  if (typeof document === 'undefined') return emptyScopes()
  return readTokenScopes(document.styleSheets)
}

function emptyScopes(): TokenScopes {
  return { light: new Map(), dark: new Map(), conditional: new Map() }
}

/** 这一条在某个条件下另有一个值。没有就返回 null。 */
export function conditionalValue(name: string, scopes: TokenScopes): string | null {
  return scopes.conditional.get(name) ?? null
}

export interface TokenPreview {
  light: string
  dark: string
  /** 深色块里重新声明过没有。没写的那几条，两档是同一个值，值得说明白。 */
  differs: boolean
}

/** 一条 token 在两档下各是多少。样式表里没有这一条时返回 null——不编一个值出来。 */
export function previewOf(name: string, scopes: TokenScopes): TokenPreview | null {
  const raw = scopes.light.get(name)
  if (raw === undefined) return null
  const darkRaw = scopes.dark.get(name)
  return {
    light: resolveIn(raw, scopes.light),
    dark: resolveIn(darkRaw ?? raw, scopes.dark, scopes.light),
    differs: darkRaw !== undefined,
  }
}

const HEX = /^#(?:[\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i
const COLOR_FUNCTION = /^(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\(/i
const NAMED = new Set(['transparent', 'white', 'black', 'currentcolor'])

/** 这个值是不是一个颜色。判据只有「能不能涂出来」，不猜它的用途。 */
export function isColorValue(value: string): boolean {
  const v = value.toLowerCase()
  return HEX.test(v) || COLOR_FUNCTION.test(v) || NAMED.has(v)
}

/** 样式表里声明过的名字，按声明顺序。`prefix` 给出就只留这一族。 */
export function tokenNames(scopes: TokenScopes, prefix?: string): string[] {
  const names = [...scopes.light.keys()]
  return prefix ? names.filter((name) => name.startsWith(prefix)) : names
}

/** 声明过的颜色 token，按声明顺序。 */
export function colorTokens(scopes: TokenScopes): string[] {
  return tokenNames(scopes).filter((name) => {
    const preview = previewOf(name, scopes)
    return preview !== null && isColorValue(preview.light)
  })
}

/** 一个值末尾那一段数值，`660px` → `660`。用来把 `--z-*` 那一梯按高低排。 */
export function numericValue(value: string): number | null {
  const hit = /(-?\d+(?:\.\d+)?)/.exec(value)
  return hit ? Number(hit[1]) : null
}

/* -------------------------------------------------------------- 颜色分节 */

export interface ColorSection {
  name: string
  note: string
  names: string[]
}

/**
 * 颜色分成哪几节。`prefixes` 里给的是**这个名字本身或它开头那一段**：`--ok` 连
 * `--ok-ink`、`--ok-wash` 一起收，`--fill` 连 `--fill-2`。
 *
 * 分组只按名字前缀认，不另抄一份名单：产品新加一条 `--chart-9`，它自己会落进「额度与
 * 图表」。一条都对不上的落进最后一节「其余」，摆出来而不是假装没有——`demoTokens.spec.ts`
 * 拿真的 `src/style.css` 跑一遍，断言「其余」是空的（不空就是新加了一条前缀没人认领）。
 */
const COLOR_SECTIONS: ReadonlyArray<{ name: string; note: string; prefixes: string[] }> = [
  {
    name: '中性阶',
    note: '灰带一点琥珀的色相。--line-2 不只是分隔线，也是选中行的底色——对比度按它算。',
    prefixes: ['--ink', '--text', '--muted', '--faint', '--line', '--fill', '--canvas', '--surface', '--raised'],
  },
  {
    name: '主色与状态',
    note: '琥珀只用在关键处。状态色分三件：点是点、字是字、底是底，互不换用。',
    prefixes: ['--accent', '--ok', '--warn', '--danger', '--signal-yellow', '--signal-blue'],
  },
  {
    name: '品牌',
    note: '品牌漆：首页方块悬停、选中时的底色，和压在上面的图形。两套主题同一个值——那一格是标志本身，不是界面底色，所以不用跟着主题变的 --accent。',
    prefixes: ['--brand-'],
  },
  {
    name: '代码色',
    note: '代码块在两套主题下都是反色的：深底浅字。行内代码用 --fill，不是反色块。设备实时终端同理，两套主题同一个深底。',
    prefixes: ['--code-', '--terminal-'],
  },
  {
    name: '头像',
    note: '两种头像：队友的方块一个队友一个深色、按 handle 挑；首字母头像（人、团队、空间、项目）的底色由 seed 算出来，两套主题同一个值，所以字也是定值。都不用琥珀——琥珀留给关键动作。',
    prefixes: ['--agent-', '--avatar-'],
  },
  {
    name: '额度与图表',
    note: '额度条的四段和文档图表的八条序列。序列到八为止，第九条折进「其他」，不新开一个颜色。',
    prefixes: ['--usage-', '--chart-'],
  },
  {
    name: '浮层与装饰',
    note: '浮层、插销和装饰的那一组：遮罩、焦点圈、滚动条、反色块、品牌光晕，和左侧 rail 上标出当前项目的竖条。',
    prefixes: [
      '--focus-ring',
      '--overlay',
      '--selection-',
      '--scrollbar-',
      '--lightbox-',
      '--video-',
      '--inverse-',
      '--logo-',
      '--category-',
      '--rail-mark',
    ],
  },
]

/**
 * 这个名字算不算在那个前缀底下。
 *
 * `--code-` 这种自己带尾巴的，就是「以它开头」；`--fill` 这种不带尾巴的，还得连上
 * `--fill-2` 一起收。差别不能糊掉：给 `--code-` 再补一个连字符会得到 `--code--`，
 * 一条都匹配不上（而这件事在页面上是看不出来的，只表现为一半颜色掉进「其余」）。
 */
function underPrefix(name: string, prefix: string): boolean {
  return prefix.endsWith('-') ? name.startsWith(prefix) : name === prefix || name.startsWith(`${prefix}-`)
}

/** 按上面那张表把颜色分节，顺序也照它。空的那几节不收。 */
export function groupColors(scopes: TokenScopes): ColorSection[] {
  const sections: ColorSection[] = COLOR_SECTIONS.map((section) => ({
    name: section.name,
    note: section.note,
    names: [],
  }))
  const rest: string[] = []
  for (const name of colorTokens(scopes)) {
    const index = COLOR_SECTIONS.findIndex((section) => section.prefixes.some((prefix) => underPrefix(name, prefix)))
    if (index === -1) rest.push(name)
    else sections[index].names.push(name)
  }
  const filled = sections.filter((section) => section.names.length)
  return rest.length ? [...filled, { name: '其余', note: '还没归类的颜色。', names: rest }] : filled
}
