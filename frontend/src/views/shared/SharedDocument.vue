<!--
  经链接分享的一份文档（#3774），不用登录。读链接里的那一段，取文档的当前版本，
  画面在 SharedDocumentView.vue。
-->
<template>
  <SharedDocumentView :doc="doc" :error="error" />
</template>

<script setup lang="ts">
/**
 * 正文里的图片是资料库里的相对路径：换成分享页能取的地址，不登录也能看到。文档里的
 * 小程序从这条链接取代码，离开这一页时换回原来的取法。
 */
import type { SharedDocument } from '@/api/documentShares'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import SharedDocumentView from './SharedDocumentView.vue'

import { readSharedDocument, sharedDocApps, sharedImageUrl } from '@/api/documentShares'
import { t } from '@/i18n'
import { answerDocAppsWith, docApps } from '@/lib/docApps'

const props = defineProps<{ token: string }>()

const doc = ref<SharedDocument | null>(null)
const error = ref('')

/** `![说明](library/图.png)` 里的相对路径换成分享页能取的地址；已经是网址的不动。 */
function withSharedImages(markdown: string, token: string): string {
  return markdown.replace(/(!\[[^\]]*\]\()([^)\s]+)(\))/g, (whole, open: string, src: string, close: string) =>
    /^(?:[a-z]+:|\/)/i.test(src) ? whole : `${open}${sharedImageUrl(token, src.replace(/^\.\//, ''))}${close}`
  )
}

const before = docApps()
// A link is given to someone: search engines do not list the page (the API sends X-Robots-Tag).
const robots = document.createElement('meta')
robots.name = 'robots'
robots.content = 'noindex, nofollow'
onMounted(() => document.head.append(robots))
onBeforeUnmount(() => {
  robots.remove()
  if (before) answerDocAppsWith(before)
})

watch(
  () => props.token,
  async (token) => {
    answerDocAppsWith(sharedDocApps(token))
    doc.value = null
    error.value = ''
    try {
      const found = await readSharedDocument(token)
      doc.value = { ...found, content: withSharedImages(found.content, token) }
    } catch (cause) {
      const status = (cause as { status?: number } | null)?.status
      error.value = status === 404 ? t('publicSite.sharedDoc.gone') : t('publicSite.sharedDoc.loadFailed')
    }
  },
  { immediate: true }
)
</script>
