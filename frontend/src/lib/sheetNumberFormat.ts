// 表格里一个数按它自己的格式显示成什么字。
//
// 一格在文件里存的是 `386200`，屏幕上该是 `386,200`；存的是 `0.041`，格式是 `0.0%` 时
// 该是 `4.1%`。预览要是只把数原样吐出来，一张财务表读起来就和一串裸数字一样——列与列
// 之间对不齐，百分数还得读者自己换算。这里只做「读者看得见的那一层」：读文件里那一格
// 的 `numFmt`，把数排成 Excel 排出来的样子。
//
// 覆盖的是 `0`、`0.00`、`#,##0`、`#,##0.00`、`0%`、`0.0%`、`#,##0.0%` 这一族，加上
// 前后缀字面量（`¥#,##0.00`、`#,##0.00 元`）。认不出来的格式一律退回数本身：宁可少
// 一层排场，也不要为了好看把数改错。科学计数法、分数、会计用的大括号负数那种都归到
// 这一档。

/** 一段格式串拆开之后要的那几件。`grouping` 是千分位，`decimals` 是小数点后几位，
 *  `percent` 是百分数（数要先乘 100）。 */
interface Shape {
  decimals: number
  grouping: boolean
  percent: boolean
  /** 正数、负数各自的前后缀字面量。负数在格式里用 `;` 分段写时用第二段。 */
  prefix: string
  suffix: string
  negativePrefix: string
  negativeSuffix: string
}

/** 去掉格式串里的引号、反斜杠转义和方括号里的条件/颜色段（`[Red]`、`[$¥-804]`）。 */
function plain(format: string): string {
  let out = ''
  for (let i = 0; i < format.length; i += 1) {
    const ch = format[i]
    if (ch === '"') {
      const end = format.indexOf('"', i + 1)
      out += format.slice(i + 1, end === -1 ? format.length : end)
      i = end === -1 ? format.length : end
      continue
    }
    if (ch === '\\') {
      out += format[i + 1] ?? ''
      i += 1
      continue
    }
    if (ch === '[') {
      const end = format.indexOf(']', i)
      const inner = format.slice(i + 1, end === -1 ? format.length : end)
      // `[$¥-804]` 这种是带币种的货币前缀：`$` 后面、`-` 前面那一截是币种符号，要留下。
      if (inner.startsWith('$')) out += inner.slice(1).split('-')[0]
      i = end === -1 ? format.length : end
      continue
    }
    if (ch === '_') {
      // `_` 后面那个字符是「留出它的宽度」，本身不显示。
      i += 1
      continue
    }
    if (ch === '*') {
      // `*` 后面那个字符是填充符，只影响宽度。
      i += 1
      continue
    }
    out += ch
  }
  return out
}

/** 格式串里「真的是格式码」的那些字符：引号字面量、转义和方括号段都剔掉。判是不是日期
 *  要在这一份上判——`"USD"` 里的 `d` 是币种名，不是「日」。 */
function codesOnly(format: string): string {
  let out = ''
  for (let i = 0; i < format.length; i += 1) {
    const ch = format[i]
    if (ch === '"') {
      const end = format.indexOf('"', i + 1)
      i = end === -1 ? format.length : end
      continue
    }
    if (ch === '\\') {
      i += 1
      continue
    }
    if (ch === '[') {
      const end = format.indexOf(']', i)
      i = end === -1 ? format.length : end
      continue
    }
    out += ch
  }
  return out
}

/** 一段格式拆成「数字占位之前的前缀、数字占位、之后的后缀」。没有数字占位的那一段
 *  （`General`、纯文本）给 null。 */
function splitSection(section: string): { prefix: string; digits: string; suffix: string } | null {
  const match = /[0#.,]+/.exec(section)
  if (!match) return null
  return {
    prefix: section.slice(0, match.index),
    digits: match[0],
    suffix: section.slice(match.index + match[0].length),
  }
}

/** 格式串 → 要的那几件；不是我们认得的数字格式就给 null。 */
function shapeOf(format: string): Shape | null {
  const text = plain(format)
  if (!text) return null
  const sections = text.split(';')
  const body = sections[0] ?? ''
  // 日期、时间、文本、通用（`General`）都不是这里管的事。判「是不是日期」要在没有引号
  // 字面量的那一份上判：`#,##0.00 "USD"` 的 `D` 是币种名里的一个字母，不是「日」——
  // 拿带字面量的串去判，一整类带货币名的格式会被当成日期退回裸数。
  const codes = codesOnly(format)
  if (/[ymdhs]/i.test(codes)) return null
  if (body.includes('@')) return null
  // 科学计数法（`0.00E+00`）和分数（`# ?/?`）：我们排不出它们真正的样子，
  // 按普通小数排会把 1.2E+05 写成「1.20E+05」这种既不是原样也不是科学计数的东西。
  if (/[eE][+-]/.test(body) || body.includes('/')) return null
  const first = splitSection(body)
  if (!first) return null
  // 数字占位末尾的逗号在 Excel 里是「按千缩」运算符（`#,##0,` 把 12345 显示成 12），
  // 不是分组。排不出来就退回数本身，不能把数放大一千倍给人看。
  if (first.digits.endsWith(',')) return null
  const point = first.digits.indexOf('.')
  const decimals = point === -1 ? 0 : first.digits.length - point - 1
  // `%` 是不是运算符要看没被引号包住的那一份：`0.0"%"` 是在数后面加一个百分号，
  // 数本身不乘一百；`0.0%` 才是百分数。运算符那一颗从字面量里去掉，剩下的照旧留着
  // （`0.0%;(0.0%)` 的后缀是 `)`）。
  const percent = codes.includes('%')
  const negative = sections[1]
  const second = negative === undefined ? null : splitSection(negative)
  return {
    decimals,
    grouping: first.digits.includes(','),
    percent,
    prefix: first.prefix,
    // `%` 留在它原来那个位置上（`0.0%` 的后缀就是 `%`，`0.0%;(0.0%)` 的负数后缀是
    // `%)`）：它既是运算符、也是屏幕上那几个字的一部分，从中间挪到最后会把右括号挤错地方。
    suffix: first.suffix,
    negativePrefix: second ? second.prefix : `-${first.prefix}`,
    negativeSuffix: second ? second.suffix : first.suffix,
  }
}

/** 数按格式排出来。`format` 是文件里那一格的 `numFmt`；认不出来、或者这一格不是数就 null。 */
export function formatNumber(value: number, format: string | undefined): string | null {
  if (!Number.isFinite(value)) return null
  if (!format || /^general$/i.test(format)) return null
  const shape = shapeOf(format)
  if (!shape) return null
  const scaled = shape.percent ? value * 100 : value
  const negative = scaled < 0
  const abs = Math.abs(scaled)
  let body = abs.toFixed(Math.min(shape.decimals, 10))
  if (shape.grouping) {
    const [whole, fraction] = body.split('.')
    body = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : `.${fraction}`)
  }
  const prefix = negative ? shape.negativePrefix : shape.prefix
  const suffix = negative ? shape.negativeSuffix : shape.suffix
  return `${prefix}${body}${suffix}`
}

/** 这一格的显示文字该往哪边靠：文件自己写了就照它，没写时数靠右、别的靠左。 */
export function alignmentOf(horizontal: string | undefined, numeric: boolean): 'left' | 'center' | 'right' | undefined {
  if (horizontal === 'right' || horizontal === 'center' || horizontal === 'left') return horizontal
  if (horizontal === 'justify' || horizontal === 'distributed') return 'left'
  if (horizontal === 'fill') return 'left'
  return numeric ? 'right' : undefined
}
