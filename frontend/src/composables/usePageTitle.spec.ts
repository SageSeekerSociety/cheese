// 顶栏和浏览器标签写的是「当前这一页叫什么」。页面可以自己起名（私聊写对方的
// 名字，搜索写搜的词），但一页给了个空名字时，它还是那一页：标题得落回路由自己
// 的名字，不能跳到上一层或者站名。
import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { describe, expect, it } from 'vitest'

import { usePageTitle } from './usePageTitle'

import i18n from '@/i18n'

const blank = { template: '<div />' }

async function currentTitleAt(path: string, pageTitle?: string) {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      {
        path: '/projects/:id',
        name: 'project',
        component: { template: '<router-view />' },
        meta: { title: '项目工作台' },
        children: [{ path: 'dm/:peer', name: 'dm', component: blank, meta: { title: '私聊' } }],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  let read: () => string | undefined = () => undefined
  const Probe = defineComponent({
    setup() {
      const titles = usePageTitle()
      if (pageTitle !== undefined) titles.setDynamicTitle(pageTitle)
      read = () => titles.getRouteHierarchy.value.find((item) => item.title)?.title
      return () => h('div')
    },
  })
  render(Probe, { global: { plugins: [router, createPinia(), i18n] } })
  return read()
}

describe('当前页标题', () => {
  it('页面自己起了名字，就写这个名字', async () => {
    expect(await currentTitleAt('/projects/p1/dm/bob', 'Bob')).toBe('Bob')
  })

  it('页面给了空名字，写路由自己的名字，不跳到上一层', async () => {
    expect(await currentTitleAt('/projects/p1/dm/bob', '')).toBe('私聊')
  })
})
