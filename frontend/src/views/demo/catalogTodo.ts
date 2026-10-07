/**
 * 生成脚本（`scripts/catalog-scaffold.mjs`）留在骨架里的占位记号。
 *
 * 骨架给每个必填 prop 填一个类型对得上、挂得起来的值，好让人先看到这一格能画出来；
 * 但那个值是编的，不是产品里的形状。所以每一个占位都带记号，`catalog.spec.ts` 看见
 * 记号就红 —— 句子补完了、args 原样留着的骨架过不去：
 *
 *   - 字符串直接写成 `TODO_MARK`，序列化以后搜得到；
 *   - 数、布尔、null、数组、对象、函数放不下这句话，就包一层 `todo(...)`：它原样
 *     返回这个值，同时记下这一处（数组、对象、函数还会被认出来是哪一条的哪一格）。
 *
 * 单独一份文件、不放进 `catalog.ts`：目录的分册要引它，而 `catalog.ts` 反过来要引
 * 分册的值，从那里拿会绕成运行时的循环引用。
 */
export const TODO_MARK = 'TODO(catalog)'

const marked = new WeakSet<object>()
const sites: string[] = []

/** 一个还没换成真形状的占位值：原样返回，记一笔。 */
export function todo<T>(value: T): T {
  // 第 0 行是 Error 自己，第 1 行是这里，第 2 行是写着 todo(...) 的那一处。
  sites.push(new Error().stack?.split('\n')[2]?.trim() ?? TODO_MARK)
  if ((typeof value === 'object' && value !== null) || typeof value === 'function') marked.add(value)
  return value
}

/** 这个值是不是 `todo(...)` 包过的那一个（数组、对象、函数认得出；数和布尔看 `todoSites`）。 */
export function isTodo(value: unknown): boolean {
  return ((typeof value === 'object' && value !== null) || typeof value === 'function') && marked.has(value)
}

/** 到现在为止调过 `todo(...)` 的每一处（目录各分册在 import 时就全算完了）。 */
export function todoSites(): readonly string[] {
  return sites
}
