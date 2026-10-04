<script setup lang="ts">
// 手机上编辑文档时贴在键盘上方的那一条（里面是 DocBubble 的 `bar` 样子）。编辑区有焦点
// 时才在；键盘弹起时页面可见的那一块会变矮，这一条跟着它的下沿走。
import type { Editor } from '@tiptap/core'

import { onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'

const props = defineProps<{ editor: Editor }>()

const focused = ref(false)
const bottom = ref(0)

watch(
  () => toRaw(props.editor),
  (editor, _previous, cleanup) => {
    focused.value = editor.isFocused
    const onFocus = () => (focused.value = true)
    // 点这一条上的按钮时编辑区可能先失焦一下：稍等再看，别让按钮在点到之前就没了。
    let timer: ReturnType<typeof setTimeout> | undefined
    const onBlur = () => {
      clearTimeout(timer)
      timer = setTimeout(() => (focused.value = editor.isFocused), 200)
    }
    editor.on('focus', onFocus)
    editor.on('blur', onBlur)
    cleanup(() => {
      clearTimeout(timer)
      editor.off('focus', onFocus)
      editor.off('blur', onBlur)
    })
  },
  { immediate: true }
)

// 键盘挡住的那一截：布局视口的下沿到可见视口的下沿。
function place() {
  const view = window.visualViewport
  bottom.value = view ? Math.max(0, window.innerHeight - view.height - view.offsetTop) : 0
}
onMounted(() => {
  place()
  window.visualViewport?.addEventListener('resize', place)
  window.visualViewport?.addEventListener('scroll', place)
})
onBeforeUnmount(() => {
  window.visualViewport?.removeEventListener('resize', place)
  window.visualViewport?.removeEventListener('scroll', place)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="focused"
      class="doc-keyboard-bar"
      :style="{ bottom: `${bottom}px`, '--doc-keyboard-bar-bottom': `${bottom}px` }"
    >
      <slot />
    </div>
  </Teleport>
</template>

<style scoped>
.doc-keyboard-bar {
  position: fixed;
  right: 0;
  left: 0;
  z-index: var(--z-overlay);
  display: flex;
  padding-bottom: env(safe-area-inset-bottom);
  background: var(--surface);
}
</style>
