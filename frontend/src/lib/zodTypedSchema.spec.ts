// 我们自己维护的 zod 4 → vee-validate 适配层。它替代的是只认 zod 3 的
// `@vee-validate/zod`，所以这里盯的是 vee-validate 真正会调用的三个方法：
// parse 的路径归组、cast 的默认值、describe 的必填判断。
import { describe, expect, it } from 'vitest'
import { z } from 'zod'

import { toTypedSchema } from './zodTypedSchema'

const schema = z.object({
  name: z.string().min(1, { message: '要填名字' }),
  tags: z.array(z.string().min(1, { message: '标签不能为空' })),
  nested: z.object({ inner: z.string() }),
  optional: z.string().optional(),
  withDefault: z.string().default('默认'),
  nestedDefault: z.object({ inner: z.string().default('里层默认') }),
})

describe('toTypedSchema 的 parse', () => {
  it('通过时把解析后的值交回去', async () => {
    const typed = toTypedSchema(z.object({ n: z.coerce.number() }))
    const result = await typed.parse({ n: '3' })
    expect(result.errors).toEqual([])
    expect(result.value).toEqual({ n: 3 })
  })

  it('把错误按表单路径归组，数组下标写成 tags[0]', async () => {
    const typed = toTypedSchema(schema)
    const result = await typed.parse({
      name: '',
      tags: ['', 'ok'],
      nested: { inner: 'x' },
      nestedDefault: { inner: 'x' },
    })
    expect(result.errors).toEqual([
      { path: 'name', errors: ['要填名字'] },
      { path: 'tags[0]', errors: ['标签不能为空'] },
    ])
  })

  it('联合类型先记成员的说法，再记联合自己那条（与官方适配层一致）', async () => {
    const typed = toTypedSchema(z.object({ b: z.union([z.string().min(5, { message: '太短' }), z.number()]) }))
    const result = await typed.parse({ b: true } as unknown as { b: string | number })
    // zod 4 里成员 issue 自己不带路径，要落到联合所在的字段上，界面才显示得出来。
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].path).toBe('b')
    expect(result.errors[0].errors).toEqual([
      'Invalid input: expected string, received boolean',
      'Invalid input: expected number, received boolean',
      'Invalid input',
    ])
  })

  it('成员自己的路径接在联合所在的字段后面（联合在嵌套字段里）', async () => {
    const typed = toTypedSchema(z.object({ o: z.union([z.object({ x: z.string() }), z.object({ y: z.number() })]) }))
    const result = await typed.parse({ o: { x: 1 } } as unknown as { o: { x: string } | { y: number } })
    expect(result.errors.map((e) => e.path)).toEqual(['o.x', 'o.y', 'o'])
  })
})

describe('toTypedSchema 的 cast', () => {
  it('解析得过就返回解析后的值', () => {
    const typed = toTypedSchema(schema)
    expect(typed.cast!({ name: 'a', tags: [], nested: { inner: 'i' } })).toMatchObject({ name: 'a' })
  })

  it('解析不过时补上 schema 里的默认值，嵌套对象逐层补', () => {
    const typed = toTypedSchema(schema)
    expect(typed.cast!({ name: 'a' })).toMatchObject({
      name: 'a',
      withDefault: '默认',
      nestedDefault: { inner: '里层默认' },
    })
  })

  it('显式给的 null 留在原地，不被默认值顶掉', () => {
    const typed = toTypedSchema(z.object({ a: z.string().default('x') }))
    expect(typed.cast!({ a: null })).toEqual({ a: null })
    expect(typed.cast!({})).toEqual({ a: 'x' })
  })
})

describe('toTypedSchema 的 describe', () => {
  const typed = toTypedSchema(schema)

  it('必填字段回答 required', () => {
    expect(typed.describe!('name')).toEqual({ required: true, exists: true })
  })

  it('可选字段不 required', () => {
    expect(typed.describe!('optional')).toEqual({ required: false, exists: true })
  })

  it('带默认值的字段不 required', () => {
    expect(typed.describe!('withDefault')).toEqual({ required: false, exists: true })
  })

  it('嵌套与数组下标路径都能找到', () => {
    expect(typed.describe!('nested.inner')).toEqual({ required: true, exists: true })
    expect(typed.describe!('tags[0]')).toEqual({ required: true, exists: true })
  })

  it('路径不存在时 exists 为 false', () => {
    expect(typed.describe!('nope' as never)).toEqual({ required: false, exists: false })
  })
})

// 挂到真实的 useForm 上再验一遍：适配层的方法是照着 vee-validate 的调用姿势写的，
// 只测方法本身的返回值，接错了地方也看不出来。
import { useForm } from 'vee-validate'

describe('toTypedSchema 接到 useForm 上', () => {
  it('字段错误按注册的名字出现，默认值进到表单值里', async () => {
    const form = useForm({
      initialValues: { name: '', count: undefined as number | undefined },
      validationSchema: toTypedSchema(
        z.object({
          name: z.string().min(1, { message: '要填名字' }),
          count: z.number().min(1, { message: '要填数量' }),
        })
      ),
    })
    const result = await form.validate()
    expect(result.valid).toBe(false)
    expect(form.errors.value.name).toBe('要填名字')
    // 没填的数字是类型错误（undefined 不是 number），不是那条 min 的说法。
    expect(form.errors.value.count).toBe('Invalid input: expected number, received undefined')

    form.setValues({ name: 'a', count: 3 })
    const passed = await form.validate()
    expect(passed.valid).toBe(true)
  })
})

describe('toTypedSchema 接到 useForm 上的默认值', () => {
  it('cast 把 schema 的 .default() 补进初始值', () => {
    const form = useForm({
      validationSchema: toTypedSchema(z.object({ flag: z.boolean().default(false), name: z.string() })),
    })
    expect(form.values.flag).toBe(false)
  })
})
