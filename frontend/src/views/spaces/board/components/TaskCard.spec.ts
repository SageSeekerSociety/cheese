// 卡片上的出处标。这一条钉的是**卡片上看得见什么**，不是数据层有什么 ——
// `store.spec.ts` 已经钉过映射（前缀被摘掉、`origin` 有值），但「摘干净了没有」「老题
// 上会不会多出一块空白」是两块独立的失败方式，只有把卡片真画出来才算数：
//
// 1. **从 PDF 来的题**：出处进 `origin`、正文里那串给机器认的字被摘掉，卡片上正好
//    出现一次（当标），不是两次（标 + 正文）。
// 2. **旧数据（简介里没有那串前缀的手写题）**：那一格整块不出现 —— 不是空标、不是
//    「未知」，是一片真的空。老题目占真库的绝大多数，这里错了就是全站多一块灰。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import { toBoardTask } from '../store'

import TaskCard from './TaskCard.vue'

/** 真 `Task` 的字段子集 —— `toBoardTask` 与卡片只读这些。 */
function task(over: Record<string, unknown>) {
  return {
    id: 1,
    name: '一道题',
    intro: '题干',
    approved: 'APPROVED',
    participantLimit: 0,
    minTeamSize: 1,
    maxTeamSize: 1,
    deadline: null,
    createdAt: Date.now(),
    participants: { total: 3, examples: [] },
    creator: { id: 4, username: 'caisongyang', nickname: '蔡松洋' },
    ...over,
  }
}

/** 卡片画进真 Vuetify + 一条真路由里 —— 它用 `v-card :to` 和 `useRoute`，
 *  两样都得在。 */
const Host = defineComponent({
  props: { spec: { type: Object, required: true } },
  setup(props) {
    return () =>
      h(TaskCard as Component, {
        task: toBoardTask(props.spec as never),
      })
  },
})

async function draw(spec: Record<string, unknown>) {
  const stub = { render: () => h('div') }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
    ],
  })
  await router.push('/spaces/11/board')
  await router.isReady()
  await render(Host, {
    props: { spec },
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
}

function originChip(): Element | null {
  return document.querySelector('.tcard__origin')
}

afterEach(cleanup)

describe('题目卡上的出处标', () => {
  it('从 PDF 来的题：卡片上出现「PDF · 第 3 页」，正文里那串字一次都不出现', async () => {
    await draw(task({ intro: '【PDF · 第 3 页】实现一个缓存' }))

    expect(originChip()?.textContent).toContain('PDF · 第 3 页')
    // 「正好一次」：标上那次算，正文里不该再有一次。
    expect(document.querySelector('.tcard__summary')?.textContent).toBe('实现一个缓存')
    expect(document.querySelector('.tcard__summary')?.textContent).not.toContain('PDF')
  })

  it('旧数据（手写的题，简介里没有那串前缀）：那一格整块不出现，也没有「未知」', async () => {
    await draw(task({ intro: '实现一个缓存' }))

    expect(originChip()).toBeNull()
    // 空标、「未知」都算多出来一块 —— 老题目占真库的绝大多数。所以看**画出来的
    // 东西**：那一格里一个标都没有、一个字都没有（源码里的注释不算数）。
    expect(document.querySelectorAll('.tcard__tags .v-chip').length).toBe(0)
    expect(document.querySelector('.tcard__tags')?.textContent?.replace(/\s+/g, '')).toBe('')
  })
})

// 卡片上另外两格：挂着几份材料（份数，不是清单）和题目的标签。两格都是**画出来
// 才算数**的东西 —— 映射对不对在 `store.spec.ts`，这里钉的是「0 份时会不会画出
// 『附件 0』」「标签画成什么样子」。
describe('题目卡上的材料份数与标签', () => {
  function fileChip(): Element | null {
    return document.querySelector('.tcard__files')
  }

  function tagTexts(): string[] {
    return Array.from(document.querySelectorAll('.tcard__tag')).map((el) =>
      (el.textContent || '').replace(/\s+/g, '').trim()
    )
  }

  it('挂了两份材料就写「附件 2」', async () => {
    await draw(task({ attachmentCount: 2 }))

    expect(fileChip()?.textContent?.replace(/\s+/g, '')).toBe('附件2')
  })

  it('一份材料都没有时那一格整块不出现，不是「附件 0」', async () => {
    await draw(task({}))

    expect(fileChip()).toBeNull()
    expect(document.body.textContent).not.toContain('附件')
  })

  it('题目的标签一枚一枚写成 #名字', async () => {
    await draw(
      task({
        topics: [
          { id: 1, name: '缓存' },
          { id: 2, name: '并发' },
        ],
      })
    )

    expect(tagTexts()).toEqual(['#缓存', '#并发'])
  })

  it('一个标签都没有时那片区域是空的（不出现一枚空标）', async () => {
    await draw(task({}))

    expect(tagTexts()).toEqual([])
  })
})
