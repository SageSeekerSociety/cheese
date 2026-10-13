// 话题页左右两栏怎么分：对话一栏、工作面板一栏，中间一条可拖的分隔。
//
// 面板有三档：收起、并排、铺满（专注模式，spec §7.1）。铺满时对话不消失，收成左边
// 一条窄边：上面一颗按钮回到并排，对话里有新回复时它带一个点——对话让开了，可人还在
// 这件事里，有人回话要看得出来。
//
// 宽度记在 store 里（和侧栏的宽度同一份记录），跨会话保留；专注模式只在这一次会话里。
import type { ComputedRef, Ref } from 'vue'

import { computed, ref, watch } from 'vue'

import { useTopicMemory } from '@/composables/useTopicMemory'

import { useCommands } from '@/commands'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export function useTopicPanes(opts: { panelOpen: Ref<boolean>; panelFloat: Ref<boolean>; desktop: Ref<boolean> }) {
  const store = useWorkspaceStore()
  const { focusMode } = useTopicMemory()

  // 专注模式是「对话让开、面板占满」：面板没并排开着时它不成立，一进这种状态就关掉，
  // 免得从宽档带来的那个开关和这里的布局打架。
  const docked: ComputedRef<boolean> = computed(() => !opts.panelFloat.value && opts.panelOpen.value)
  watch(
    docked,
    (on) => {
      if (!on) focusMode.value = false
    },
    { immediate: true }
  )

  // 对话收着的这段时间里，有没有新的回复。回到并排就清掉。
  const chatNews = ref(false)
  watch(focusMode, (on) => {
    if (!on) chatNews.value = false
  })
  function noteChatNews() {
    if (focusMode.value) chatNews.value = true
  }

  function toggleFocus() {
    focusMode.value = !focusMode.value
  }
  // 铺满那颗按钮的图标和说法，跟着现在是哪一档变。
  const focusIcon = computed(() => (focusMode.value ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'))
  const focusLabel = computed(() => (focusMode.value ? t('work.room.menu.exitFocus') : t('work.room.menu.focus')))

  useCommands(() =>
    opts.desktop.value && docked.value
      ? [
          {
            id: 'room.focus',
            title: focusLabel.value,
            icon: focusIcon.value,
            run: toggleFocus,
          },
        ]
      : []
  )

  // 对话那一栏的宽度：面板并排开着时是 `0 0 N%`（可拖的分隔），否则它吃掉整宽——面板
  // 收着，或浮在上面。
  const chatStyle = computed(() => (docked.value ? { flex: `0 0 ${store.chatPct}%` } : { flex: '1 1 0', minWidth: 0 }))

  // 收起 / 拉开的那一下里，栏在变窄变宽，里面的东西不跟着变：几百条消息每一帧按新
  // 宽度重新折行，既费又难看。把里面钉在这一栏落定时的宽度上，栏只是把它裁开、露出。
  function freezeChatWidth(el: Element) {
    const panes = (el as HTMLElement).parentElement
    if (!panes) return
    ;(el as HTMLElement).style.setProperty('--chat-frozen-w', `${(panes.clientWidth * store.chatPct) / 100}px`)
  }

  // 拖分隔线：对话这一栏的宽度按整排的百分比记。
  function startPaneDrag(e: MouseEvent) {
    const panes = (e.currentTarget as HTMLElement).parentElement
    if (!panes) return
    const rect = panes.getBoundingClientRect()
    const move = (ev: MouseEvent) => {
      store.setChatPct(((ev.clientX - rect.left) / rect.width) * 100)
    }
    const stop = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', stop)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', stop)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  return {
    focusMode,
    focusIcon,
    focusLabel,
    docked,
    chatNews,
    noteChatNews,
    toggleFocus,
    chatStyle,
    freezeChatWidth,
    startPaneDrag,
  }
}
