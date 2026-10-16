// 话题头部 ⋯ 里那两份用量：什么时候去取、什么时候算新鲜、换了话题手里那份怎么办。
import type { Ref } from 'vue'
import type { UsageStats } from '@/cx_types'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicUsage = vi.fn()
const getProjectUsage = vi.fn()

vi.mock('@/api', () => ({
  getTopicUsage: (...a: unknown[]) => getTopicUsage(...a),
  getProjectUsage: (...a: unknown[]) => getProjectUsage(...a),
}))

import { useTopicUsage } from './useTopicUsage'

const usage = (turns: number): UsageStats => ({
  input_tokens: turns * 100,
  output_tokens: turns * 50,
  total_tokens: turns * 150,
  cost_usd: turns / 100,
  turns,
  unpriced_tokens: 0,
})

/** 让取数那一串 promise 落地。 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

let now = 1_700_000_000_000

beforeEach(() => {
  now = 1_700_000_000_000
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  getTopicUsage.mockImplementation(async (id: string) => usage(id === 'topic-1' ? 1 : 2))
  getProjectUsage.mockImplementation(async () => usage(9))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

interface Fixture {
  topic: Ref<UsageStats | null>
  project: Ref<UsageStats | null>
  loading: Ref<boolean>
  load: () => Promise<void>
  setTopic: (id: string) => void
}

/** 摆成组件那个样子：话题是外面传进来的，取数由宿主在要用时叫。 */
function mountUsage(topicId = 'topic-1'): Fixture {
  const source = ref(topicId)
  const target: Fixture = {
    topic: ref<UsageStats | null>(null),
    project: ref<UsageStats | null>(null),
    loading: ref(false),
    load: async () => {},
    setTopic: (next) => {
      source.value = next
    },
  }
  render(
    defineComponent({
      setup() {
        const used = useTopicUsage({ topicId: source, projectId: () => 'p1' })
        target.topic = used.topic
        target.project = used.project
        target.loading = used.loading
        target.load = used.load
        return () => h('div')
      },
    })
  )
  return target
}

describe('话题用量', () => {
  it('一次取两份：这个话题的，和它项目的', async () => {
    const f = mountUsage()
    await f.load()
    expect(getTopicUsage).toHaveBeenCalledWith('topic-1')
    expect(getProjectUsage).toHaveBeenCalledWith('p1')
    expect(f.topic.value?.turns).toBe(1)
    expect(f.project.value?.turns).toBe(9)
    expect(f.loading.value).toBe(false)
  })

  it('同一个话题十秒以内不取第二遍，手里的旧数照旧留着；过了十秒才重取', async () => {
    const f = mountUsage()
    await f.load()
    now += 3_000
    await f.load()
    expect(getTopicUsage).toHaveBeenCalledTimes(1)
    // 跳过重取不是把数抹掉：悬停一次紧接着点开，卡里还是刚才那份。
    expect(f.topic.value?.turns).toBe(1)

    now += 8_000
    await f.load()
    expect(getTopicUsage).toHaveBeenCalledTimes(2)
  })

  it('换了话题，上一个话题的数不挂在这一个上', async () => {
    const f = mountUsage()
    await f.load()
    expect(f.topic.value?.turns).toBe(1)

    f.setTopic('topic-2')
    await nextTick()
    expect(f.topic.value).toBeNull()
    expect(f.project.value).toBeNull()
  })

  it('换回去是重新取，不是拿十秒以内那份旧的顶上', async () => {
    const f = mountUsage()
    await f.load()
    f.setTopic('topic-2')
    await nextTick()
    f.setTopic('topic-1')
    await nextTick()
    now += 5_000

    await f.load()
    expect(getTopicUsage).toHaveBeenCalledTimes(2)
    expect(f.topic.value?.turns).toBe(1)
  })

  it('半路才回来的旧话题结果不落地', async () => {
    let land: (u: UsageStats) => void = () => {}
    getTopicUsage.mockImplementationOnce(
      () =>
        new Promise<UsageStats>((resolve) => {
          land = resolve
        })
    )
    const f = mountUsage()
    const pending = f.load()
    f.setTopic('topic-2')
    await nextTick()

    land(usage(1))
    await pending
    await flush()
    expect(f.topic.value).toBeNull()
  })
})
