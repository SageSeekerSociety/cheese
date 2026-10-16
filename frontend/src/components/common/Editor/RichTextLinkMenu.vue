<script setup lang="ts">
// 链接弹层：地址 + 是否新窗口。打开时带出光标所在链接的地址；选中一段文字就是给它
// 上链接，光标里空着就是插一段地址本身。
import { inject, reactive, watch } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

import BaseButton from '@/components/base/BaseButton.vue'

const { state, run, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))

const link = reactive({ href: '', newTab: true })
watch(open, (isOpen) => {
  if (!isOpen) return
  const attrs = state.value.editor?.getAttributes('link') ?? {}
  link.href = (attrs.href as string | undefined) ?? ''
  link.newTab = attrs.target ? attrs.target === '_blank' : true
})

function applyLink() {
  const href = link.href.trim()
  open.value = false
  if (!href) return
  const target = link.newTab ? '_blank' : '_self'
  const editor = state.value.editor
  if (!editor) return
  if (editor.state.selection.empty && !editor.isActive('link')) {
    run((c) => c.insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href, target } }] }))
  } else {
    run((c) => c.extendMarkRange('link').setLink({ href, target }))
  }
}

function removeLink() {
  pick((c) => c.extendMarkRange('link').unsetLink())
}
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start" :close-on-content-click="false">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb"
        :aria-label="label('link')"
        :title="label('link')"
        :aria-pressed="state.link"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-link-variant</v-icon>
      </button>
    </template>
    <form class="rt-pop rt-pop--form" @submit.prevent="applyLink">
      <v-text-field
        v-model="link.href"
        :label="label('linkHref')"
        placeholder="https://"
        density="compact"
        variant="outlined"
        hide-details
        autofocus
        autocomplete="off"
      />
      <v-checkbox v-model="link.newTab" :label="label('linkNewTab')" density="compact" hide-details />
      <div class="rt-pop__actions">
        <BaseButton v-if="state.link" kind="ghost" size="sm" @click="removeLink">{{ label('unlink') }}</BaseButton>
        <BaseButton type="submit" kind="primary" size="sm">{{ label('linkApply') }}</BaseButton>
      </div>
    </form>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
