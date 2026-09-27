// The pure half of the demo components: a very small expression language for
// what a fence declares, and the value a simulation shows for one set of
// slider positions.
//
// Both sides of the build use this file. build.mjs prerenders every component's
// default state, so the demo reads with JavaScript off; src/demo-dom.mjs
// recomputes it on every input. One implementation is what keeps the two from
// drifting apart — a demo must not say one thing before its script loads and
// another after.
//
// The language is deliberately tiny, because it has to be explained in a
// fence a person writes by hand:
//
//   numbers, `true` / `false`, "strings", and the names the fence declares
//   + - * / %   and   ( )       arithmetic
//   < <= > >= == !=             comparison, one per expression
//   !  &&  ||                   logic, with `&&` binding tighter than `||`

const TWO = ['>=', '<=', '==', '!=', '&&', '||']
const ONE = '+-*/%()<>!'

function lex(src) {
  const out = []
  let i = 0
  while (i < src.length) {
    const c = src[i]
    if (/\s/.test(c)) { i++; continue }
    const two = src.slice(i, i + 2)
    if (TWO.includes(two)) { out.push({ k: 'op', v: two }); i += 2; continue }
    if (ONE.includes(c)) { out.push({ k: 'op', v: c }); i++; continue }
    const n = /^\d+(?:\.\d+)?/.exec(src.slice(i))
    if (n) { out.push({ k: 'num', v: parseFloat(n[0]) }); i += n[0].length; continue }
    const w = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))
    if (w) { out.push({ k: 'name', v: w[0] }); i += w[0].length; continue }
    if (c === '"' || c === "'") {
      const end = src.indexOf(c, i + 1)
      if (end < 0) throw new Error(`少一个收尾的 ${c}`)
      out.push({ k: 'str', v: src.slice(i + 1, end) })
      i = end + 1
      continue
    }
    throw new Error(`看不懂的字符「${c}」`)
  }
  return out
}

export function truthy(v) {
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  if (typeof v === 'string') return v !== '' && v !== '0' && v !== 'false'
  return !!v
}

function asNum(v, what) {
  if (typeof v === 'number') return v
  if (typeof v === 'boolean') return v ? 1 : 0
  const n = Number(v)
  if (Number.isNaN(n)) throw new Error(`${what} 不是数字：${v}`)
  return n
}

// Evaluate one expression against `vars`. Throws on anything the language does
// not have — the build turns that into a failed build, not a wrong number.
export function evaluate(src, vars = {}) {
  const t = lex(String(src ?? ''))
  let i = 0
  const at = (v) => t[i] && t[i].k === 'op' && t[i].v === v
  const atom = () => {
    const x = t[i]
    if (!x) throw new Error('式子没写完')
    i++
    if (x.k === 'num' || x.k === 'str') return x.v
    if (x.k === 'name') {
      if (!(x.v in vars)) throw new Error(`没有这个参数：${x.v}`)
      return vars[x.v]
    }
    if (x.v === '(') { const v = or(); if (!at(')')) throw new Error('少一个 )'); i++; return v }
    throw new Error(`这里不该有「${x.v}」`)
  }
  const unary = () => {
    if (at('-')) { i++; return -asNum(unary(), '负号后面') }
    if (at('!')) { i++; return !truthy(unary()) }
    return atom()
  }
  const mul = () => {
    let v = unary()
    for (;;) {
      if (at('*')) { i++; v = asNum(v, '* 左边') * asNum(unary(), '* 右边') } else if (at('/')) { i++; v = asNum(v, '/ 左边') / asNum(unary(), '/ 右边') } else if (at('%')) { i++; v = asNum(v, '% 左边') % asNum(unary(), '% 右边') } else return v
    }
  }
  const add = () => {
    let v = mul()
    for (;;) {
      if (at('+')) { i++; v = asNum(v, '+ 左边') + asNum(mul(), '+ 右边') } else if (at('-')) { i++; v = asNum(v, '- 左边') - asNum(mul(), '- 右边') } else return v
    }
  }
  const cmp = () => {
    const a = add()
    const x = t[i]
    if (!x || x.k !== 'op' || !['<', '<=', '>', '>=', '==', '!='].includes(x.v)) return a
    i++
    const b = add()
    switch (x.v) {
      case '<': return asNum(a, '< 左边') < asNum(b, '< 右边')
      case '<=': return asNum(a, '<= 左边') <= asNum(b, '<= 右边')
      case '>': return asNum(a, '> 左边') > asNum(b, '> 右边')
      case '>=': return asNum(a, '>= 左边') >= asNum(b, '>= 右边')
      case '==': return a === b
      default: return a !== b
    }
  }
  const and = () => { let v = cmp(); while (at('&&')) { i++; const b = cmp(); v = truthy(v) && truthy(b) } return v }
  const or = () => { let v = and(); while (at('||')) { i++; const b = and(); v = truthy(v) || truthy(b) } return v }
  const out = or()
  if (i < t.length) throw new Error(`多余的「${t[i].v}」`)
  return out
}

// 12,300 rather than 12300: these numbers are read, not computed with.
export function num(n) {
  if (!Number.isFinite(n)) return String(n)
  const r = Math.round(n * 100) / 100
  const [head, tail] = String(r).split('.')
  return head.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (tail ? `.${tail}` : '')
}

export function show(v) {
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'number') return num(v)
  return String(v ?? '')
}

// `{key}` in a rule's text becomes that parameter's value.
export function fill(text, vars) {
  return String(text ?? '').replace(/\{(\w+)\}/g, (m, k) => (k in vars ? show(vars[k]) : m))
}

// What one simulation shows: the parameters as declared, the ordered rules
// (each marked hit or not, so the component can show why the others did not
// apply), and the readouts computed from the parameters.
// The parameters as they stand, plus everything the fence derives from them.
// Fences derive values so a rule can be about something computed (`spent >=
// total`) without the language needing to grow a `let`.
export function parameters(spec, values = {}) {
  const vals = {}
  for (const v of spec.vars || []) vals[v.key] = v.key in values ? values[v.key] : v.value
  for (const d of spec.derived || []) vals[d.key] = evaluate(d.expr, vals)
  return vals
}

export function simulate(spec, values) {
  const vals = parameters(spec, values)
  const out = (spec.out || []).map((o) => {
    let value
    try { value = evaluate(o.expr, vals) } catch (e) { value = `（${e.message}）` }
    return { label: o.label, unit: o.unit || '', value, text: show(value) }
  })
  const hit = (spec.rules || []).findIndex((r) => r.when === undefined || truthy(evaluate(r.when, vals)))
  const rules = (spec.rules || []).map((r, i) => ({ label: r.label || '', text: fill(r.text, vals), tone: r.tone || '', hit: i === hit }))
  return { vars: vals, out, rules, hit }
}
