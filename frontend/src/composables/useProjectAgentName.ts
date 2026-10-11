// 项目默认那位 AI 队友的名字，给拿不到房间名册、又要在句子里称呼它的地方用（章程、
// 记忆、周报、例行、邮件草稿、资料库）。项目可以给它改名，所以这些句子不能写死「芝士」。
// 孤立渲染（单测里没有 pinia）时退回默认名。
import type { ComputedRef } from 'vue'

import { computed, getCurrentInstance } from 'vue'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export function useProjectAgentName(): ComputedRef<string> {
  const workspace = getCurrentInstance()?.appContext.config.globalProperties.$pinia ? useWorkspaceStore() : null
  return computed(() => workspace?.agentName ?? t('shell.agentDefaultName'))
}
