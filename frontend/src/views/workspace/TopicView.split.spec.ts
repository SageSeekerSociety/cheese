/// <reference types="node" />
// 话题视图这一页的拆分边界。
//
// `TopicView.vue` 曾经 974 行，逼近 `.claude/scripts/check-file-sizes.py` 的 1000 行红线。
// 拆不改行为：对外还是同一个路由入口、同一组 props、同一份模板，行为由同目录那六个
// `.spec.ts` 锁着（它们一行没改，拆分后照样通过）。这一份锁的是另一件事——那些职责真的
// 搬走了，而且搬出去之后每一块自己也不许再长成下一个 974 行。
//
// 扫源码而不是挂载：断言的是「哪几件事在哪个文件里」，那是文件本身说的事；挂载这一页
// 要拖进来 Vuetify 和十几次接口调用，只为读出一个文件几行。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

/** 一个文件几行，和 `wc -l` 一个算法：末尾那个换行不算一行。 */
const lines = (text: string) => text.split('\n').length - 1

const PAGE = 'TopicView.vue'
/** 按职责拆出去的那几块，和它们各自管的事。 */
const MODULES: readonly [string, string][] = [
  ['topicView/shared.ts', '共用的类型：这个地址指着谁，两块组件往回够的那几处'],
  ['topicView/address.ts', '地址：打开哪一页、哪一段，面板开着哪一格'],
  ['topicView/panel.ts', '右侧面板的收 / 开和那条 Esc 栈'],
  ['topicView/conversation.ts', '这一段对话：支线、任务页、署名'],
  ['topicView/header.ts', '页头和这一页的标题'],
  ['topicView/live.ts', '这个房间此刻是不是活的'],
  ['topicView/actions.ts', '对话栏和面板回上来的那几件事各归哪一处'],
]

const page = read(PAGE)

describe('话题视图的拆分边界', () => {
  it('主文件和拆出去的每一块都在 650 行以内', () => {
    for (const [file] of MODULES) expect([file, lines(read(file)) <= 650]).toEqual([file, true])
    expect([PAGE, lines(page) <= 650]).toEqual([PAGE, true])
  })

  it('主文件把这几件事交给了同目录的那几个模块', () => {
    for (const [file] of MODULES) {
      const name = file.split('/')[1]!.replace(/\.ts$/, '')
      expect([name, page.includes(`from './topicView/${name}'`) || page.includes(`'./topicView/${name}'`)]).toEqual([
        name,
        true,
      ])
    }
  })

  it('那些职责真的搬走了，没有在主文件里留一份', () => {
    // 每一处都是拆之前主文件自己 new 出来的：地址、面板、支线、任务页、标题、实时状态。
    for (const gone of [
      'useRoomTabHistory(',
      'useTopicPanel(',
      'useChannelThreads(',
      'useTaskPage(',
      'usePageTitle(',
      'useConversationOpening(',
      'roomMembersQuery(',
    ]) {
      expect([gone, page.includes(gone)]).toEqual([gone, false])
    }
  })

  it('对外还是同一个路由入口：props 和模板都还在这一页', () => {
    expect(page).toContain('defineProps<{ projectId: string; topicId: string; taskId?: string; threadId?: string }>()')
    expect(page).toContain("defineOptions({ name: 'TopicView' })")
    expect(page).toContain('<template>')
    // 支线那一半仍然是这里异步取的：它是这一页模板里画的，不是搬走的那几块之一。
    expect(page).toContain("defineAsyncComponent(() => import('@/views/workspace/ThreadPane.vue'))")
  })

  it('每一块自己交出它的那几个入口，主文件只做接线', () => {
    const exports: readonly [string, string][] = [
      ['topicView/address.ts', 'export function useTopicAddress('],
      ['topicView/panel.ts', 'export function useTopicPanelState('],
      ['topicView/conversation.ts', 'export function useTopicConversation('],
      ['topicView/header.ts', 'export function useTopicHeader('],
      ['topicView/live.ts', 'export function useTopicLive('],
      ['topicView/actions.ts', 'export function useTopicActions('],
    ]
    for (const [file, name] of exports) expect([file, read(file).includes(name)]).toEqual([file, true])
  })
})
