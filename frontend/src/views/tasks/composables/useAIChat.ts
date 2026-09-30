import type { TaskAIAdviceConversationContext } from '@/network/api/tasks/types'

import { provide, readonly, ref } from 'vue'

import { useEvents } from '../events'

import { t } from '@/i18n'

export function useAIChat() {
  const selectedContext = ref<TaskAIAdviceConversationContext | undefined>()
  const events = useEvents()
  const chatDialogOpen = ref(false)

  events.on('chat-dialog-open', (value) => {
    chatDialogOpen.value = value.status
  })

  const clearContext = () => {
    selectedContext.value = undefined
  }

  const getDisplayName = (section: TaskAIAdviceConversationContext['section'], index: number) => {
    if (section === 'knowledge_fields') {
      return t('tasks.advice.section.knowledge', { n: index + 1 })
    }
    if (section === 'learning_paths') {
      return t('tasks.advice.section.learningPath', { n: index + 1 })
    }
    if (section === 'methodology') {
      return t('tasks.advice.section.methodology', { n: index + 1 })
    }
    if (section === 'team_tips') {
      return t('tasks.advice.section.teamRole', { n: index + 1 })
    }
    return null
  }

  const openChat = (
    section: TaskAIAdviceConversationContext['section'],
    index: number,
    question?: string,
    displayName?: string
  ) => {
    selectedContext.value = {
      section,
      index,
      displayName: displayName || getDisplayName(section, index),
    }

    events.emit('chat-dialog-open', { status: true, question })
  }

  const openGeneralChat = () => {
    clearContext()
    events.emit('chat-dialog-open', { status: true })
  }

  // 提供对话方法给子组件
  provide('aiChat', {
    openChat: readonly(
      (section: TaskAIAdviceConversationContext['section'], index: number, question?: string, displayName?: string) =>
        openChat(section, index, question, displayName)
    ),
    openGeneralChat: readonly(openGeneralChat),
  })

  return {
    chatDialogOpen,
    selectedContext,
    clearContext,
    openChat,
    openGeneralChat,
  }
}
