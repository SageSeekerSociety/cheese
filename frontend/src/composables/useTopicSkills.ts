// 技能广场：这间房点亮了哪些技能，以及点亮之后它们此刻能不能真跑起来。
//
// 点亮的名字落在这间房上（后端 `topics.skills`），下一轮起这间房里每一个 agent 的
// 提示词都会带上它们的说明书 —— 所以这边只需要「列出来、点一下、存起来」，把说明书
// 送到执行机那边是后端的事。
//
// **打开广场时才读**，不做常驻拉取：没人打开的时候多读一次，只是一次没人看的网络
// 调用，而技能就那几颗，打开再读也就一瞬间。健康那一列同理，一次打开探一遍，后端
// 自己还会缓存（`GET /topics/{id}/skills/{name}/health`，5 分钟）。
import type { SkillHealth, TopicSkill } from '../api/projectSkills'

import { ref } from 'vue'

import { getSkillHealth, listTopicSkills, setTopicSkills } from '../api/projectSkills'

import { t } from '@/i18n'

export function useTopicSkills(topicId: () => string | null, onError: (message: string) => void) {
  const topicSkills = ref<TopicSkill[]>([])
  const skillHealth = ref<Record<string, SkillHealth>>({})
  const skillsLoading = ref(false)

  /** 打开广场：读列表，顺手把每颗的健康探一遍。 */
  async function openSkills() {
    const id = topicId()
    if (!id) return
    skillsLoading.value = true
    skillHealth.value = {}
    try {
      topicSkills.value = (await listTopicSkills(id)).data
    } catch (e) {
      if (topicId() === id) topicSkills.value = []
      onError(e instanceof Error ? e.message : t('work.room.skills.loadFailed'))
      skillsLoading.value = false
      return
    }
    if (topicId() !== id) return
    skillsLoading.value = false
    // 一颗探不到就说「未知」——不该让一颗探不到的把整个广场挡住。
    await Promise.all(
      topicSkills.value.map(async (s) => {
        try {
          const health = await getSkillHealth(id, s.name)
          if (topicId() === id) skillHealth.value = { ...skillHealth.value, [s.name]: health }
        } catch {
          // 未知：那一颗的圆点留着灰的，别的照常。
        }
      })
    )
  }

  /** 点亮 / 熄灭一颗。先改本地再落库，落库失败就翻回去 —— 一颗按钮不该等一个来回。 */
  async function toggleSkill(name: string) {
    const id = topicId()
    if (!id) return
    const before = topicSkills.value
    const next = before.map((s) => (s.name === name ? { ...s, enabled: !s.enabled } : s))
    topicSkills.value = next
    try {
      const saved = await setTopicSkills(
        id,
        next.filter((s) => s.enabled).map((s) => s.name)
      )
      if (topicId() !== id) return
      const on = new Set(saved.enabled)
      topicSkills.value = next.map((s) => ({ ...s, enabled: on.has(s.name) }))
    } catch (e) {
      if (topicId() !== id) return
      topicSkills.value = before
      onError(e instanceof Error ? e.message : t('work.room.skills.saveFailed'))
    }
  }

  return { topicSkills, skillHealth, skillsLoading, openSkills, toggleSkill }
}
