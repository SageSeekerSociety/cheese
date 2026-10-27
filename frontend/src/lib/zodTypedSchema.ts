/**
 * 把 zod 4 的 schema 接到 vee-validate 4 上。
 *
 * vee-validate 官方的 `@vee-validate/zod` 只认 zod 3：`cast()` 把 `_def.defaultValue`
 * 当函数调用、`describe()` 读 `_def.typeName`，而 zod 4 里默认值是一个值、类型名换成了
 * `_def.type`，所以表单里只要有一个 `.default()` 字段，`useForm` 一建就抛 TypeError。
 * 官方适配层没有支持 zod 4 的版本（vee-validate 5 还是 beta），这一层由我们自己维护。
 *
 * vee-validate 只用到 TypedSchema 的三个方法：
 * - `parse`：校验，并把每条错误按表单路径归组（路径写成 `a.b[0].c`）；
 * - `cast`：把初始值补上 schema 里的 `.default()`；
 * - `describe`：回答某个路径上的字段是否必填（`meta.required`）。
 *
 * 内部实现只用 zod 4 公开的类与 `unwrap()`，不读 zod 3 那套 `_def.typeName`。
 */
import type { TypedSchema, TypedSchemaError } from 'vee-validate'
import type { ZodIssue, ZodType } from 'zod'

import { z } from 'zod'

type FormPath = readonly (string | number)[]

/** vee-validate 的路径写法：`a.b[0].c`。 */
function formPath(segments: FormPath): string {
  let path = ''
  for (const segment of segments) {
    if (typeof segment === 'number') path += `[${segment}]`
    else path += path ? `.${segment}` : String(segment)
  }
  return path
}

/** 把 `parent` 和 `child` 两截表单路径接起来。 */
function joinPath(parent: string, child: string): string {
  if (!parent) return child
  if (!child) return parent
  return child.startsWith('[') ? `${parent}${child}` : `${parent}.${child}`
}

/** 把一条 issue 记进按路径归组的错误袋。联合类型先记成员各自的说法，再记联合自己那条
 *  ——和官方适配层的做法一致。zod 4 里成员 issue 的路径是相对联合本身的，所以一路
 *  带着父路径往下接，界面才能按注册的字段名找到它。 */
function addIssue(bag: Record<string, TypedSchemaError>, issue: ZodIssue, inheritedPath = ''): void {
  const path = joinPath(inheritedPath, formPath(issue.path as FormPath))
  if (issue.code === 'invalid_union' && issue.errors.length) {
    for (const nested of issue.errors) for (const member of nested) addIssue(bag, member, path)
    if (!path) return
  }
  const entry = bag[path] ?? (bag[path] = { path, errors: [] })
  entry.errors.push(issue.message)
}

/** 去掉 optional / nullable / default / readonly / catch 这些包装，拿到里面的类型。 */
function unwrap(schema: ZodType): ZodType {
  let current = schema
  while (
    current instanceof z.ZodOptional ||
    current instanceof z.ZodNullable ||
    current instanceof z.ZodDefault ||
    current instanceof z.ZodReadonly ||
    current instanceof z.ZodCatch
  ) {
    // `unwrap()` 在类型上给的是 core 的 `$ZodType`，运行时仍是同一个 classic 实例。
    current = current.unwrap() as unknown as ZodType
  }
  return current
}

/** 一个对象 schema 里各字段的 `.default()`，嵌套对象逐层取。 */
function defaultsOf(schema: ZodType): Record<string, unknown> {
  const object = unwrap(schema)
  if (!(object instanceof z.ZodObject)) return {}
  const defaults: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(object.shape) as [string, ZodType][]) {
    if (child instanceof z.ZodDefault) {
      const parsed = child.safeParse(undefined)
      if (parsed.success) defaults[key] = parsed.data
      continue
    }
    if (child instanceof z.ZodObject) {
      const nested = defaultsOf(child)
      if (Object.keys(nested).length) defaults[key] = nested
    }
  }
  return defaults
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && !(value instanceof Date)
}

/** 把默认值合进已有的值，嵌套对象逐层合；已有的值优先。 */
function mergeDefaults(defaults: Record<string, unknown>, values: Record<string, unknown>): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...values }
  for (const [key, value] of Object.entries(defaults)) {
    const given = merged[key]
    merged[key] = isPlainObject(value) && isPlainObject(given) ? mergeDefaults(value, given) : given ?? value
  }
  return merged
}

/** 把 `a.b[0].c` 拆回路径段。 */
function parsePath(path: string): FormPath {
  return path
    .split(/\.|\[(\d+)\]/)
    .filter((segment) => segment !== undefined && segment !== '')
    .map((segment) => (/^\d+$/.test(segment) ? Number(segment) : segment))
}

/** 沿路径找 schema，找不到返回 null。 */
function schemaAt(schema: ZodType, path: string): ZodType | null {
  let current: ZodType | null = schema
  for (const segment of parsePath(path)) {
    if (!current) return null
    const node = unwrap(current)
    if (node instanceof z.ZodObject) {
      current = (node.shape[segment as string] as ZodType | undefined) ?? null
      continue
    }
    if (node instanceof z.ZodArray && typeof segment === 'number') {
      current = node.element as ZodType
      continue
    }
    return null
  }
  return current
}

export function toTypedSchema<TSchema extends ZodType>(
  schema: TSchema
): TypedSchema<z.input<TSchema>, z.output<TSchema>> {
  return {
    __type: 'VVTypedSchema',
    async parse(values) {
      const result = await schema.safeParseAsync(values)
      if (result.success) return { value: result.data, errors: [] }
      const bag: Record<string, TypedSchemaError> = {}
      for (const issue of result.error.issues) addIssue(bag, issue)
      return { errors: Object.values(bag) }
    },
    cast(values) {
      const result = schema.safeParse(values)
      if (result.success) return result.data
      if (!isPlainObject(values)) return values as z.input<TSchema>
      return mergeDefaults(defaultsOf(schema), values) as z.input<TSchema>
    },
    describe(path) {
      if (!path) return { required: !schema.isOptional(), exists: true }
      const found = schemaAt(schema, path)
      if (!found) return { required: false, exists: false }
      return { required: !found.isOptional(), exists: true }
    },
  } as TypedSchema<z.input<TSchema>, z.output<TSchema>>
}
