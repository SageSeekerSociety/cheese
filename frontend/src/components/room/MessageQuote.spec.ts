/**
 * 对话里挂着的那一份引用。
 *
 * 各种引用共用一个框：文字引用要看得见引的是哪一段，位置引用要看得见指在哪儿。
 * 每一样都不是装饰——受话人靠它回原文件里找那一处。
 */
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import MessageQuote from './MessageQuote.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

it('整页文字引用：报出页码、文件身份和原文', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'slide-page' as const,
        path: 'deck.pptx',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        page: 3,
        text: '这一段不对',
      },
    },
  })

  expect(container.textContent).toContain('引用第 3 页文字')
  expect(container.textContent).toContain('deck.pptx')
  expect(container.textContent).toContain('已提交文件')
  expect(container.textContent).toContain('v7')
  expect(container.textContent).toContain('这一段不对')
})

it('页上一点：报出第几页、指在哪儿，不装作有原文', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'page-pin' as const,
        path: 'deck.pdf',
        source: 'live' as const,
        version: 'v7',
        task_id: null,
        page: 5,
        x: 0.42,
        y: 0.17,
      },
    },
  })

  expect(container.textContent).toContain('指了第 5 页的一处')
  expect(container.textContent).toContain('从左 42%、从上 17%')
  expect(container.textContent).toContain('当前工作文件')
  expect(container.querySelector('.message-quote__text')).toBeNull()
})

it('表格一格：报出地址和这一格当时的内容', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'sheet-cell' as const,
        path: 'budget.xlsx',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        sheet: 'Sheet1',
        address: 'B7',
        value: '1200',
      },
    },
  })

  expect(container.textContent).toContain('引用单元格 Sheet1!B7')
  expect(container.textContent).toContain('budget.xlsx')
  expect(container.textContent).toContain('1200')
})

it('表格一格：CSV 没有工作表名，地址就是它自己', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'sheet-cell' as const,
        path: '名单.csv',
        source: 'live' as const,
        version: 'v7',
        task_id: null,
        sheet: '',
        address: 'A3',
        value: '',
      },
    },
  })

  expect(container.textContent).toContain('引用单元格 A3')
  // 空格子不装作有内容。
  expect(container.textContent).toContain('（空）')
})

it('正文一段：报出在哪一节、引的是哪一段原文', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'text-range' as const,
        path: '说明.md',
        source: 'live' as const,
        version: 'v7',
        task_id: null,
        text: '失败以后重试 3 次',
        heading: '配置',
        prefix: '退避',
        suffix: '，超过就报错',
      },
    },
  })

  expect(container.textContent).toContain('引用文档里的一段文字')
  expect(container.textContent).toContain('标题「配置」')
  expect(container.textContent).toContain('失败以后重试 3 次')
})

it('认不出的 kind 不当作页码算：画得出一句「一份引用」，不崩', () => {
  const { container } = render(MessageQuote, {
    props: {
      // 更新的客户端可能已经发来这一版还不认识的引用。
      quote: {
        kind: 'video-frame',
        path: 'clip.mp4',
        source: 'live',
        version: 'v7',
        task_id: null,
      } as unknown as import('../../lib/quotedContext').QuotedContext,
    },
  })

  expect(container.textContent).toContain('一份引用')
  expect(container.textContent).toContain('clip.mp4')
  expect(container.textContent).not.toContain('undefined')
})

it('网页元素：报出这一处的名字、文字和文件身份，选择器只留给芝士', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'web-element' as const,
        path: 'report.html',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        selector: 'body > main > p:nth-of-type(2)',
        tag: 'p',
        text: '这一句说错了',
        rect: { x: 12, y: 40, w: 300, h: 24 },
        viewport: { w: 1024, h: 768 },
      },
    },
  })

  expect(container.textContent).toContain('引用了页面上的一处')
  expect(container.textContent).toContain('report.html')
  // 老消息没带名字：按标签说是一处段落，不把选择器念给人听。
  expect(container.textContent).toContain('网页里的一处段落')
  expect(container.textContent).not.toContain('nth-of-type')
  expect(container.textContent).toContain('这一句说错了')
})

it('网页元素带了名字（所在那一节的标题）就写名字', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'web-text' as const,
        path: 'report.html',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        selector: '#app > div > aside > section',
        tag: 'section',
        label: '二、时间安排',
        text: '十月底前完成',
        prefix: '',
        suffix: '',
        rect: { x: 12, y: 40, w: 300, h: 24 },
        viewport: { w: 1024, h: 768 },
      },
    },
  })
  expect(container.textContent).toContain('二、时间安排')
  expect(container.textContent).not.toContain('#app')
})

it('网页框选：只报网址，不装作有文件身份', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'web-region' as const,
        url: 'https://app.tunnel.example/dashboard',
        rect: { x: 0, y: 0, w: 200, h: 100 },
        viewport: { w: 800, h: 600 },
      },
    },
  })

  expect(container.textContent).toContain('圈出了页面上的一块区域')
  expect(container.textContent).toContain('https://app.tunnel.example/dashboard')
  // 应用没有版本：不写文件身份那一行，也不冒出 undefined。
  expect(container.querySelector('.message-quote__path')).toBeNull()
  expect(container.querySelector('.message-quote__identity')).toBeNull()
  expect(container.textContent).not.toContain('undefined')
})

it('图片上框的一块：报出原图像素的位置和文件身份', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'image-region' as const,
        path: '示意图.png',
        source: 'live' as const,
        version: 'v8',
        task_id: null,
        x: 12,
        y: 40,
        width: 180,
        height: 96,
        natural_width: 1024,
        natural_height: 768,
      },
    },
  })

  expect(container.textContent).toContain('圈出了图片上的一块区域')
  expect(container.textContent).toContain('示意图.png')
  // 位置是原图像素，不是比例——图片有一份不变的原图。
  expect(container.textContent).toContain('x=12、y=40、宽=180、高=96')
  expect(container.textContent).toContain('当前工作文件')
  expect(container.textContent).not.toContain('undefined')
})

it('表格一片：报出表格软件那个 A1:C3，和这片里的内容', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'sheet-range' as const,
        path: 'budget.xlsx',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        sheet: 'Sheet1',
        start: 'B2',
        end: 'D4',
        text: '90\t80\n70\t60',
      },
    },
  })

  expect(container.textContent).toContain('引用区域 Sheet1!B2:D4')
  expect(container.textContent).toContain('budget.xlsx')
  expect(container.textContent).toContain('90')
})

it('表格一片：CSV 没有工作表名，区域就是它自己', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'sheet-range' as const,
        path: '名单.csv',
        source: 'live' as const,
        version: 'v7',
        task_id: null,
        sheet: '',
        start: 'A1',
        end: 'C3',
        // 表格里圈一片空格子，摊平出来的就是这个形状：几个制表符和换行。
        text: '\t\t\n\t\t',
      },
    },
  })

  expect(container.textContent).toContain('引用区域 A1:C3')
  // 一片里什么都没有时不装作有内容 —— 几个制表符不是「有内容」。
  expect(container.textContent).toContain('（空）')
})

it('「在看哪一份」：说这是发消息时开着的文件，不编来源和版本', () => {
  const { container } = render(MessageQuote, {
    props: { quote: { kind: 'viewing' as const, path: 'output/招募方案.docx', task_id: null } },
  })
  expect(container.textContent).toContain('发这条消息时在看这份文件')
  expect(container.textContent).toContain('output/招募方案.docx')
  expect(container.textContent).not.toContain('undefined')
  expect(container.querySelector('.message-quote__identity')).toBeNull()
})
