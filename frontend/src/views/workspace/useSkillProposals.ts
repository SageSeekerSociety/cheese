// 那段对话里那张技能提议卡的数据：芝士在这段对话提议、还在等人的那些，以及保存和拒绝。
//
// 卡片组件只管画（components 下不许取数），取数放在这里，由聊天栏调用。
import type { Ref } from 'vue'
import type { ProjectSkill } from '@/api/projectSkills'

import { ref } from 'vue'

import { confirmProjectSkill, declineProjectSkill, listProjectSkills } from '@/api/projectSkills'
import { t } from '@/i18n'
import { keys } from '@/query/keys'
import { fromSnapshot, lastSeen, seen } from '@/query/snapshot'

export function useSkillProposals(projectId: Ref<string>, conversationId: Ref<string>) {
  /** 这段对话里芝士提议、还在等人的那些。 */
  const proposals = ref<ProjectSkill[]>([])
  /** 这一轮里刚保存的：卡就地变成一句「已保存」，直到人离开这个页面。 */
  const saved = ref<ProjectSkill[]>([])
  const busy = ref('')
  const error = ref('')

  /** 读一次。拉不到就当没有：这张卡是顺路问一句，不该让对话栏报错。 */
  async function load() {
    const conversation = conversationId.value
    const key = keys.roomSkillProposals(conversation)
    // 先画上一次在这段对话看到的那几张，读回来再换（见 query/snapshot 的 `lastSeen`）。
    proposals.value = lastSeen<ProjectSkill[]>(key) ?? []
    try {
      // 只有进这段对话时的第一次读用快照里那一块（query/snapshot），之后每次都问服务器。
      const got = await fromSnapshot(key, async () =>
        (await listProjectSkills(projectId.value)).data.filter(
          (s) => s.state === 'draft' && s.proposal && s.source_topic_id === conversation
        )
      )
      seen(key, got)
      if (conversationId.value === conversation) proposals.value = got
    } catch {
      if (conversationId.value === conversation) proposals.value = []
    }
  }

  async function act(s: ProjectSkill, run: () => Promise<ProjectSkill>, failed: string) {
    busy.value = s.id
    error.value = ''
    try {
      const row = await run()
      proposals.value = proposals.value.filter((p) => p.id !== s.id)
      const key = keys.roomSkillProposals(conversationId.value)
      seen(
        key,
        (lastSeen<ProjectSkill[]>(key) ?? []).filter((p) => p.id !== s.id)
      )
      return row
    } catch (e) {
      error.value = e instanceof Error ? e.message : failed
      return null
    } finally {
      busy.value = ''
    }
  }

  async function save(s: ProjectSkill) {
    const row = await act(s, () => confirmProjectSkill(s.id), t('work.skills.confirmFailed'))
    if (row) saved.value = [...saved.value, row]
  }

  async function decline(s: ProjectSkill) {
    await act(s, () => declineProjectSkill(s.id), t('work.skills.discardFailed'))
  }

  return { proposals, saved, busy, error, load, save, decline }
}
