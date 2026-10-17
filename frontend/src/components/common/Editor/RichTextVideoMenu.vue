<script setup lang="ts">
// 视频弹层：一条 B 站链接，插成一块播放器。认不出的链接不插，留在框里让人改。
import { inject, reactive, watch } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

import BaseButton from '@/components/base/BaseButton.vue'
import { videoEmbed } from '@/lib/docSchema/video'

const { state, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))

const video = reactive({ href: '', unsupported: false })
watch(open, (isOpen) => {
  if (isOpen) Object.assign(video, { href: '', unsupported: false })
})

function insertVideo() {
  const src = video.href.trim()
  if (!src) return
  if (!videoEmbed(src)) {
    video.unsupported = true
    return
  }
  pick((c) => c.insertContent({ type: 'video', attrs: { src } }))
}
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start" :close-on-content-click="false">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb"
        :aria-label="label('video')"
        :title="label('video')"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-play-box-outline</v-icon>
      </button>
    </template>
    <form class="rt-pop rt-pop--form" @submit.prevent="insertVideo">
      <v-text-field
        v-model="video.href"
        :label="label('videoHref')"
        placeholder="https://www.bilibili.com/video/"
        density="compact"
        variant="outlined"
        autofocus
        autocomplete="off"
        :error-messages="video.unsupported ? label('videoUnsupported') : undefined"
        :hide-details="!video.unsupported"
        @update:model-value="video.unsupported = false"
      />
      <div class="rt-pop__actions">
        <BaseButton type="submit" kind="primary" size="sm">{{ label('videoInsert') }}</BaseButton>
      </div>
    </form>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
