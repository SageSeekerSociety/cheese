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
