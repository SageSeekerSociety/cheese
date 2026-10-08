// 渲染 KaTeX 需要的样式跟着渲染代码走，不再由首屏全局带上。
import 'katex/dist/katex.min.css'

import DOMPurify from 'dompurify'
import edjsParser from 'editorjs-parser'
import katex from 'katex'

import Prism from '@/utils/prism'

type NestedListItem = {
  content: string
  items: NestedListItem[]
}

type NestedListData = {
  style: 'unordered' | 'ordered'
  items: NestedListItem[]
}

const makeDom = (tag: string, classes: string | string[], attributes: Record<string, any> = {}) => {
  const dom = document.createElement(tag)
  if (Array.isArray(classes)) {
    dom.classList.add(...classes)
  } else {
    dom.classList.add(classes)
  }
  Object.keys(attributes).forEach((key) => {
    Reflect.set(dom, key, attributes[key])
    // dom[key] = attributes[key]
  })
  return dom
}

const addChildrenList = (parent: HTMLElement, items: NestedListItem[], style: 'unordered' | 'ordered') => {
  const itemBody = parent.querySelector('.cdx-nested-list__item-body')!
  const sublistWrapper = makeListWrapper(style, ['cdx-nested-list__item-children'])

  appendItems(sublistWrapper, items, style)

  itemBody.appendChild(sublistWrapper)
}

const createItem = (content: string, items: NestedListItem[], style: 'unordered' | 'ordered') => {
  const itemWrapper = makeDom('li', 'cdx-nested-list__item')
  const itemBody = makeDom('div', 'cdx-nested-list__item-body')
  const itemContent = makeDom('div', 'cdx-nested-list__item-content', { innerHTML: content })

  itemBody.appendChild(itemContent)
  itemWrapper.appendChild(itemBody)

  if (items && items.length > 0) {
    addChildrenList(itemWrapper, items, style)
  }

  return itemWrapper
}

const appendItems = (parent: HTMLElement, items: NestedListItem[], style: 'unordered' | 'ordered') => {
  items.forEach((item) => {
    const itemElement = createItem(item.content, item.items, style)
    parent.appendChild(itemElement)
  })
}

const makeListWrapper = (style: 'unordered' | 'ordered', classes: string[] = []) => {
  const wrapperTag = style === 'unordered' ? 'ul' : 'ol'
  const wrapperStyle = style === 'unordered' ? 'cdx-nested-list--unordered' : 'cdx-nested-list--ordered'
  return makeDom(wrapperTag, ['cdx-nested-list', ...classes, wrapperStyle])
}

const customParsers = {
  math(data: any) {
    return katex.renderToString(data.math, {
      throwOnError: false,
      displayMode: true,
    })
  },
  nestedList(data: NestedListData) {
    const wrapper = makeListWrapper(data.style)

    if (data.items.length > 0) {
      appendItems(wrapper, data.items, data.style)
    } else {
      appendItems(wrapper, [{ content: '', items: [] }], data.style)
    }

    return wrapper.outerHTML
  },
  code(data: { code: string; language: string; showlinenumbers: boolean; showCopyButton: boolean }) {
    console.log(data)
    const showLineNumbers = data.showlinenumbers ? 'line-numbers' : ''
    const showCopyButton = data.showCopyButton ? 'copy-button' : ''
    const container = makeDom('div', ['code-container'])
    const preEl = makeDom('pre', [showLineNumbers, showCopyButton])
    const codeEl = makeDom('code', [`language-${data.language}`], { innerHTML: data.code })
    preEl.appendChild(codeEl)
    container.appendChild(preEl)
    Prism.highlightAllUnder(container)
    console.log(container)
    return container.innerHTML
  },
}

const parser = new edjsParser(null, customParsers)

export const parse = (data: any) => {
  // 题目和回答的 Editor.js JSON 存在服务端，取回后原样交给这里渲染。`code` 块和
  // nestedList 的 `item.content` 都是当作 HTML 插进 DOM 的，里面的内容由发问、答题
  // 的人自己填，不清理就是存储型 XSS —— 打开题目或回答的人无需交互就会执行脚本。
  // 出口统一过一道白名单，而不是在两个 `v-html` 前各过一道：消费 `parse()` 的只有
  // 「题目详情」和「回答卡片」两个组件，卡在这个口上以后新增渲染点也不会漏。
  return DOMPurify.sanitize(parser.parse(data, customParsers))
}
