// 技能广场那点会算错的事：点亮一颗之后，落到库里的**是这间房点亮的全部名字**
// （整份覆盖，不是只发这一颗），以及落库失败时本地要翻回去。广场画成什么样、按钮
// 摆在哪，浏览器里看得到，这里不管。
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/projectSkills', () => ({
  listTopicSkills: vi.fn(),
  setTopicSkills: vi.fn(),
  getSkillHealth: vi.fn(),
}))

import { getSkillHealth, listTopicSkills, setTopicSkills } from '../api/projectSkills'

import { useTopicSkills } from './useTopicSkills'

import { setLocale } from '@/i18n'

const SKILLS = [
  { name: 'wolfram', description: '算', enabled: false },
  { name: 'documents', description: '写', enabled: true },
]

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(listTopicSkills)
    .mockReset()
    .mockResolvedValue({ data: SKILLS, total: 2 } as never)
  vi.mocked(setTopicSkills)
    .mockReset()
    .mockImplementation(async (_id, enabled) => ({ enabled }))
  vi.mocked(getSkillHealth).mockReset().mockResolvedValue({ status: 'ok', detail: '' })
})

describe('技能广场', () => {
  it('打开时读列表，并给每颗探一次健康', async () => {
    const s = useTopicSkills(
      () => 't1',
      () => {}
    )

    await s.openSkills()

    expect(s.topicSkills.value.map((x) => x.name)).toEqual(['wolfram', 'documents'])
    expect(s.skillHealth.value.wolfram?.status).toBe('ok')
    expect(s.skillsLoading.value).toBe(false)
  })

  it('点一颗：本地立刻亮，落库的是这间房点亮的全部名字', async () => {
    const s = useTopicSkills(
      () => 't1',
      () => {}
    )
    await s.openSkills()

    await s.toggleSkill('wolfram')

    expect(s.topicSkills.value.find((x) => x.name === 'wolfram')?.enabled).toBe(true)
    expect(setTopicSkills).toHaveBeenCalledWith('t1', ['wolfram', 'documents'])
  })

  it('落库失败就翻回去，并把原因说出去', async () => {
    const fail = vi.fn()
    vi.mocked(setTopicSkills).mockRejectedValue(new Error('offline'))
    const s = useTopicSkills(() => 't1', fail)
    await s.openSkills()

    await s.toggleSkill('wolfram')

    expect(s.topicSkills.value.find((x) => x.name === 'wolfram')?.enabled).toBe(false)
    expect(fail).toHaveBeenCalledWith('offline')
  })

  it('健康探不到只是那一颗是灰的，列表照常出来', async () => {
    vi.mocked(getSkillHealth).mockRejectedValue(new Error('boom'))
    const s = useTopicSkills(
      () => 't1',
      () => {}
    )

    await s.openSkills()

    expect(s.topicSkills.value).toHaveLength(2)
    expect(s.skillHealth.value.wolfram).toBeUndefined()
  })
})
