// 加载骨架：一块「等会儿会长出什么」的形状。
//
// 这一份从渲染结果上问三件事，都是屏幕上看得见的：画了几行、每一行是哪一种形状、
// 以及屏幕阅读器听不听得见它在加载。像素级的尺寸（行高、缩进、头像大小）不在这里
// 断言——happy-dom 不排版，量出来的只会是我们自己写进去的那串字符串。
import type { Component } from 'vue'

import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import LoadingSkeleton from './LoadingSkeleton.vue'

const Skeleton = LoadingSkeleton as unknown as Component

/** 骨架下面每一个盒子就是一行；读屏软件那一句不是盒子，所以不会数进来。 */
function rows(container: Element): Element[] {
  return Array.from(container.querySelectorAll('.skel > div'))
}

function draw(props: Record<string, unknown>) {
  return render(Skeleton, { props }).container
}

describe('画几行', () => {
  it('要几行就画几行', () => {
    for (const variant of ['list', 'chat', 'roster', 'entry', 'text']) {
      expect(rows(draw({ variant, rows: 5 })).length, variant).toBe(5)
    }
  })

  it('不说的时候每种形态自己知道一屏画几行', () => {
    // 数字本身是「这块地方大概装得下多少」的约定：侧栏的列表最长，名册和看板的
    // 条目最短。改这几个数就是在改那个约定，所以钉住。
    expect(rows(draw({ variant: 'list' })).length).toBe(6)
    expect(rows(draw({ variant: 'chat' })).length).toBe(4)
    expect(rows(draw({ variant: 'roster' })).length).toBe(3)
    expect(rows(draw({ variant: 'entry' })).length).toBe(3)
    expect(rows(draw({ variant: 'text' })).length).toBe(3)
  })

  it('一种形态都不说的时候画的是一段正文', () => {
    const container = draw({})
    expect(rows(container).length).toBe(3)
    expect(container.querySelector('.skel__text')).not.toBeNull()
  })
})

describe('每一行长得像它替代的那一行', () => {
  it('聊天行：一个头像位 + 名字 + 正文', () => {
    const row = rows(draw({ variant: 'chat', rows: 1 }))[0]
    expect(row.querySelector('.skel__bone--avatar'), '没有头像位的话，消息到达那一刻整行会往右挪').not.toBeNull()
    expect(row.querySelector('.skel__bone--name')).not.toBeNull()
    expect(row.querySelector('.skel__bone--line')).not.toBeNull()
  })

  it('名册行：一个头像位 + 两行字 + 右边一个小标', () => {
    const row = rows(draw({ variant: 'roster', rows: 1 }))[0]
    expect(row.querySelector('.skel__bone--face')).not.toBeNull()
    expect(row.querySelector('.skel__bone--name')).not.toBeNull()
    expect(row.querySelector('.skel__bone--handle')).not.toBeNull()
    expect(row.querySelector('.skel__bone--tag')).not.toBeNull()
  })

  it('条目行：一个状态点 + 标题 + 一行元信息', () => {
    const row = rows(draw({ variant: 'entry', rows: 1 }))[0]
    expect(row.querySelector('.skel__bone--dot')).not.toBeNull()
    expect(row.querySelector('.skel__bone--line')).not.toBeNull()
    expect(row.querySelector('.skel__bone--meta')).not.toBeNull()
  })

  it('列表行和正文行只有一条线 —— 那两处真的东西上也没有头像', () => {
    for (const variant of ['list', 'text']) {
      const row = rows(draw({ variant, rows: 1 }))[0]
      expect(row.querySelectorAll('.skel__bone').length, variant).toBe(1)
      expect(row.querySelector('.skel__bone--avatar'), variant).toBeNull()
      expect(row.querySelector('.skel__bone--face'), variant).toBeNull()
    }
  })
})

describe('多行不长成一张表', () => {
  it('每一行的正文宽度不一样', () => {
    // 一排等长的灰条读起来像表格，不像文字；错落是它看着像段落的唯一线索。
    const widths = rows(draw({ variant: 'text', rows: 4 })).map(
      (r) => (r.querySelector('.skel__bone--line') as HTMLElement).style.width
    )
    expect(widths.every((w) => w !== '')).toBe(true)
    expect(new Set(widths).size).toBeGreaterThan(1)
  })
})

describe('屏幕阅读器也得知道这是在加载', () => {
  it('说得出「加载中」，而不是一堆空盒子', () => {
    // 骨架只对眼睛说话。读屏软件读到的是一堆没有文字的 div —— 和「页面空了」
    // 没有区别，所以这句话和 aria-busy 是它唯一的出口。
    const container = draw({ variant: 'chat' })
    const status = container.querySelector('[role="status"]')!
    expect(status).not.toBeNull()
    expect(status.getAttribute('aria-busy')).toBe('true')
    expect(status.textContent).toContain('加载中')
  })
})
