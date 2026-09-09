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

/** 每种形态自己那一行的类名。数「一共画了几个盒子」是不行的：有的形态除了行还
 *  画别的东西（`entry` 那条分组小标），那种盒子不是一行。 */
const ROW: Record<string, string> = {
  list: '.skel__list',
  chat: '.skel__chat',
  roster: '.skel__roster',
  entry: '.skel__entry',
  card: '.skel__card',
  site: '.skel__site',
  brief: '.skel__bblock',
  doc: '.skel__dsec',
  text: '.skel__text',
}

function rows(container: Element, variant = 'text'): Element[] {
  return Array.from(container.querySelectorAll(ROW[variant]))
}

function draw(props: Record<string, unknown>) {
  return render(Skeleton, { props }).container
}

describe('画几行', () => {
  it('要几行就画几行', () => {
    for (const variant of Object.keys(ROW)) {
      expect(rows(draw({ variant, rows: 5 }), variant).length, variant).toBe(5)
    }
  })

  it('不说的时候每种形态自己知道一屏画几行', () => {
    // 数字本身是「这块地方大概装得下多少」的约定：侧栏的列表最长，看板的卡最短。
    // 改这几个数就是在改那个约定，所以钉住。
    expect(rows(draw({ variant: 'list' }), 'list').length).toBe(6)
    expect(rows(draw({ variant: 'chat' }), 'chat').length).toBe(4)
    expect(rows(draw({ variant: 'roster' }), 'roster').length).toBe(3)
    expect(rows(draw({ variant: 'entry' }), 'entry').length).toBe(3)
    expect(rows(draw({ variant: 'card' }), 'card').length).toBe(2)
    expect(rows(draw({ variant: 'site' }), 'site').length).toBe(5)
    expect(rows(draw({ variant: 'doc' }), 'doc').length).toBe(3)
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
    const row = rows(draw({ variant: 'chat', rows: 1 }), 'chat')[0]
    expect(row.querySelector('.skel__bone--avatar'), '没有头像位的话，消息到达那一刻整行会往右挪').not.toBeNull()
    expect(row.querySelector('.skel__bone--name')).not.toBeNull()
    expect(row.querySelector('.skel__bone--line')).not.toBeNull()
  })

  it('名册行：一个头像位 + 两行字 + 右边一个小标', () => {
    const row = rows(draw({ variant: 'roster', rows: 1 }), 'roster')[0]
    expect(row.querySelector('.skel__bone--face')).not.toBeNull()
    expect(row.querySelector('.skel__bone--name')).not.toBeNull()
    expect(row.querySelector('.skel__bone--handle')).not.toBeNull()
    expect(row.querySelector('.skel__bone--tag')).not.toBeNull()
  })

  it('条目行：一个状态点 + 标题 + 一行元信息', () => {
    const row = rows(draw({ variant: 'entry', rows: 1 }), 'entry')[0]
    expect(row.querySelector('.skel__bone--dot')).not.toBeNull()
    expect(row.querySelector('.skel__bone--line')).not.toBeNull()
    expect(row.querySelector('.skel__bone--meta')).not.toBeNull()
  })

  it('条目行上面还有一条分组小标 —— 少了它，行到齐时整段会被顶下去', () => {
    // 支线进度真正长出来的是「施工中 3」这样一条小标带着它那一组的行。骨架不画
    // 小标，那 28px 就要在内容到达的那一刻凭空插进来。
    const container = draw({ variant: 'entry', rows: 1 })
    expect(container.querySelector('.skel__ehead')).not.toBeNull()
  })

  it('看板的卡是一个带框的块，不是几条浮着的线', () => {
    // 一列卡的轮廓本身就是「这儿有几件事」这个信息。只画线的话，卡到齐那一刻
    // 整列会重排一次 —— 实测过一次：骨架 54px，真卡 101px，两条换来一张。
    const row = rows(draw({ variant: 'card', rows: 1 }), 'card')[0]
    expect(row.querySelector('.skel__card-rule'), '卡里那条分隔线').not.toBeNull()
    expect(row.querySelector('.skel__bone--pill'), '负责人的头像位').not.toBeNull()
    expect(row.querySelector('.skel__bone--when'), '右端那个时间').not.toBeNull()
  })

  it('现场的一条动作：小圆点 + 动作 + 缩进的参数 + 右端的时间', () => {
    const row = rows(draw({ variant: 'site', rows: 1 }), 'site')[0]
    expect(row.querySelector('.skel__bone--sdot')).not.toBeNull()
    expect(row.querySelector('.skel__bone--verb')).not.toBeNull()
    expect(row.querySelector('.skel__bone--arg'), '不画参数那一行，一条动作就只有真的一半高').not.toBeNull()
    expect(row.querySelector('.skel__bone--when')).not.toBeNull()
  })

  it('一条活：标题 + 一行元信息 + 简报那一段，不画结论', () => {
    // 结论只有交完活的卡才有。画上它等于对每一张卡都许诺一段它多半没有的东西。
    const container = draw({ variant: 'brief' })
    expect(container.querySelector('.skel__btitle .skel__bone--dot')).not.toBeNull()
    expect(container.querySelector('.skel__bmeta')).not.toBeNull()
    expect(container.querySelectorAll('.skel__bblock').length, '只画一段').toBe(1)
  })

  it('文档：一条小标题带着几段字', () => {
    const row = rows(draw({ variant: 'doc', rows: 1 }), 'doc')[0]
    expect(row.querySelector('.skel__bone--h2')).not.toBeNull()
    expect(row.querySelectorAll('.skel__bone--p').length).toBeGreaterThan(1)
  })

  it('列表行和正文行只有一条线 —— 那两处真的东西上也没有头像', () => {
    for (const variant of ['list', 'text']) {
      const row = rows(draw({ variant, rows: 1 }), variant)[0]
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
