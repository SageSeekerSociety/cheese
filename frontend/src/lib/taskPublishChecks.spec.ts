// 「提交前」那份规则表本身。
//
// 这一份钉的是**规则表说了什么**：一条合规的表单要报空，每一条规则各自违规时要报出来
// （报的是哪一种说法也钉住），以及几条**故意不在表里**的东西不在。最后那几条与「合规」
// 那一条同样是这批的要求 —— 清单里多写一条真表单不拦的规则，就是在骗点它的人。
import { describe, expect, it } from 'vitest'

import { evaluatePublishChecks } from './taskPublishChecks'

/** 一份**合规**的表单值（发题页刚打开、三栏必填都选好了的样子）。 */
const valid = {
  name: '用 gdb 定位一次段错误',
  submitterType: 'USER',
  rank: 1,
  categoryId: 3,
  defaultDeadline: 30,
  minTeamSize: 1,
  maxTeamSize: 10,
  participantLimit: null,
  videoUrl: '',
}

/** 报出来的那些行，只取 `id`，断言好读。 */
const ids = (values: Record<string, unknown>) => evaluatePublishChecks(values).map((check) => check.id)

describe('发题「提交前」清单：规则与真表单逐条对得上', () => {
  it('三栏必填都选好、其余都合规时，一条都不报', () => {
    expect(evaluatePublishChecks(valid)).toEqual([])
  })

  it('标题：空着报，超过 100 字也报（100 字正好不报）', () => {
    expect(ids({ ...valid, name: '' })).toEqual(['name'])
    expect(ids({ ...valid, name: undefined })).toEqual(['name'])
    expect(ids({ ...valid, name: 'x'.repeat(101) })).toEqual(['name'])
    expect(ids({ ...valid, name: 'x'.repeat(100) })).toEqual([])
    // 一个空格是一格内容 —— zod 那边也是这么算的，清单不替它加码。
    expect(ids({ ...valid, name: ' ' })).toEqual([])
  })

  it('参与者类型、题目难度、所属分类：没选就各报一条', () => {
    expect(ids({ ...valid, submitterType: undefined })).toEqual(['submitterType'])
    expect(ids({ ...valid, rank: undefined })).toEqual(['rank'])
    expect(ids({ ...valid, rank: 4 })).toEqual(['rank'])
    expect(ids({ ...valid, categoryId: undefined })).toEqual(['categoryId'])
    expect(ids({ ...valid, categoryId: 0 })).toEqual(['categoryId'])
  })

  it('领取后默认天数：清空（拿到的是空串）报，30 不报', () => {
    expect(ids({ ...valid, defaultDeadline: '' })).toEqual(['defaultDeadline'])
    expect(ids({ ...valid, defaultDeadline: 30 })).toEqual([])
  })

  it('队伍人数：两栏都得 ≥ 1，上限不能小于下限', () => {
    expect(ids({ ...valid, minTeamSize: 0 })).toEqual(['teamSize'])
    expect(ids({ ...valid, maxTeamSize: '' })).toEqual(['teamSize'])
    expect(ids({ ...valid, minTeamSize: 5, maxTeamSize: 2 })).toEqual(['teamSize'])
    expect(ids({ ...valid, minTeamSize: 5, maxTeamSize: 5 })).toEqual([])
  })

  it('参与人数上限：填了就得 ≥ 1，不填（null）不报', () => {
    expect(ids({ ...valid, participantLimit: 0 })).toEqual(['participantLimit'])
    expect(ids({ ...valid, participantLimit: null })).toEqual([])
    expect(ids({ ...valid, participantLimit: 3 })).toEqual([])
  })

  it('讲解视频链接：不填放行，http / 不是地址都拦，https 放行', () => {
    expect(ids({ ...valid, videoUrl: 'https://www.bilibili.com/video/BV1xx411c7mD' })).toEqual([])
    expect(ids({ ...valid, videoUrl: 'http://example.com/video' })).toEqual(['videoUrl'])
    expect(ids({ ...valid, videoUrl: '看这个视频' })).toEqual(['videoUrl'])
  })

  it('一次违规多条时，按表单从上到下的次序报，且每条都带一句人话', () => {
    const reported = evaluatePublishChecks({ ...valid, name: '', rank: undefined, categoryId: undefined })

    expect(reported.map((check) => check.id)).toEqual(['name', 'rank', 'categoryId'])
    for (const check of reported) expect(check.text.trim().length).toBeGreaterThan(0)
    // 文案里不出现「教师 / 学生」那一套旧说法。
    expect(reported.map((c) => c.text).join(' ')).not.toMatch(/教师|学生/)
  })

  it('原型里有、真表单并不拦的，这里一条都没有', () => {
    // 「题干至少 10 个字」：真表单的 zod schema 里没有 `description` 这一项，
    // 空着题干照样发得出去 —— 所以它不该出现在清单里。
    const reported = evaluatePublishChecks({ ...valid, description: '' })
    expect(reported).toEqual([])
    const all = evaluatePublishChecks({ ...valid, name: '', rank: undefined, categoryId: undefined })
    expect(all.map((c) => c.text).join(' ')).not.toContain('题干')
  })
})
