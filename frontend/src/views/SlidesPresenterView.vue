<script setup lang="ts">
// 讲者视图那个窗口的页面：从放映那一边收稿子和页码，翻页时告诉它。画面在
// `components/panels/preview/SlidePresenter.vue`。
//
// 不取数、不读地址：频道号在自己的窗口名上（`lib/slidePresenter.ts`），稿子整份由放映那边
// 递过来。没有频道号（有人直接打开了这个地址）就说一句，让他回到幻灯片上按「讲者视图」。
import type { SlideNote } from '@/lib/slideNotes'
import type { FromPresenter, ToPresenter } from '@/lib/slidePresenter'

import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'

import SlidePresenter from '@/components/panels/preview/SlidePresenter.vue'
import { t } from '@/i18n'
import { presenterChannel, presenterIdOf } from '@/lib/slidePresenter'

const id = presenterIdOf(typeof window === 'undefined' ? '' : window.name)
const bytes = shallowRef<ArrayBuffer | null>(null)
const notes = shallowRef<SlideNote[]>([])
const title = ref('')
const page = ref(1)
const ended = ref(false)
const channel = id ? presenterChannel(id) : null

function send(message: FromPresenter) {
  channel?.postMessage(message)
}
function hear(event: MessageEvent<ToPresenter>) {
  const message = event.data
  if (message.type === 'deck') {
    bytes.value = message.deck.bytes
    notes.value = message.deck.notes
    title.value = message.deck.title
    page.value = message.deck.page
    ended.value = false
    if (typeof document !== 'undefined') document.title = `${t('slides.presenterView')} · ${message.deck.title}`
  } else if (message.type === 'page') page.value = message.page
  else if (message.type === 'end') ended.value = true
}
function go(next: number) {
  page.value = next
  if (!ended.value) send({ type: 'go', page: next })
}
function bye() {
  send({ type: 'bye' })
}

onMounted(() => {
  if (!channel) return
  channel.onmessage = hear
  send({ type: 'hello' })
  window.addEventListener('pagehide', bye)
})
onBeforeUnmount(() => {
  bye()
  window.removeEventListener('pagehide', bye)
  channel?.close()
})
</script>

<template>
  <SlidePresenter v-if="channel" :data="bytes" :notes="notes" :page="page" :title="title" :ended="ended" @go="go" />
  <div v-else class="presenter-orphan">
    <p>{{ t('slides.presenterOrphan') }}</p>
  </div>
</template>

<style scoped>
.presenter-orphan {
  display: grid;
  place-items: center;
  height: 100dvh;
  padding: 24px;
  color: var(--muted);
  text-align: center;
}
</style>
