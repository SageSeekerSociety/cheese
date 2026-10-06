// AI 队友提议的任务：列的是一段对话里还在等人决定的那些，接在这段对话后面。点「创建
// 任务」的人就是负责人。房间、任务、支线三处都用这一份，按的是正在看的那段对话 ——
// 卡落在提出它的对话上，按别的对话去取就一张也看不到。
//
// 卡片组件只管画（components 下不许取数），取数放在这里。
import type { Ref } from 'vue'

import { ref, watch } from 'vue'

import { acceptTaskProposal, dismissTaskProposal, listTaskProposals, type TaskProposal } from '@/api/tasks'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export function useTaskProposals(conversationId: Ref<string>, onCreated: (taskId: string) => void) {
  const store = useWorkspaceStore()
  const proposals = ref<TaskProposal[]>([])
  const deciding = ref<string | null>(null)

  async function load() {
    const conversation = conversationId.value
    try {
      const rows = await listTaskProposals(conversation)
      if (conversationId.value === conversation) proposals.value = Array.isArray(rows) ? rows : []
    } catch {
      // 拉不到就先不画，下一次对话有动静时再读。
    }
  }
  watch(conversationId, load, { immediate: true })

  async function decide(proposal: TaskProposal, decision: 'accept' | 'dismiss') {
    if (deciding.value) return
    deciding.value = proposal.id
    try {
      if (decision === 'accept') {
        const task = await acceptTaskProposal(conversationId.value, proposal.id)
        onCreated(task.id)
      } else {
        await dismissTaskProposal(conversationId.value, proposal.id)
      }
      proposals.value = proposals.value.filter((p) => p.id !== proposal.id)
    } catch (e) {
      store.reportError(e, t('work.task.proposal.failed'))
      void load()
    } finally {
      deciding.value = null
    }
  }

  return { proposals, deciding, load, decide }
}
