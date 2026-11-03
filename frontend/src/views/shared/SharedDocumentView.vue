<!--
  一份经链接分享的文档（#3774）：标题、什么时候更新的、当前版本的正文。只吃容器
  （SharedDocument.vue）给的数据，不取数。
-->
<template>
  <main class="shared">
    <article class="shared-column">
      <p v-if="error" class="t-reading c-muted shared-gone" role="alert">{{ error }}</p>
      <div v-else-if="!doc" class="d-flex justify-center py-10" role="status">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <template v-else>
        <h1 class="t-page-title shared-title">{{ doc.title || t('work.room.doc.untitled') }}</h1>
        <p class="t-meta-read shared-meta">{{ t('publicSite.sharedDoc.updated', { time: updated }) }}</p>
        <MarkdownView class="md-content t-reading" :source="doc.content" />
        <footer class="shared-colophon">
          <span>{{ colophonText }}</span>
          <NavLink v-if="invite" :to="invite.to" class="shared-colophon__link" data-testid="colophon-invite">
            {{ t('publicSite.sharedDoc.colophon.enter') }} →
          </NavLink>
          <NavLink v-else to="/" class="shared-colophon__link"
            >{{ t('publicSite.sharedDoc.colophon.enter') }} →</NavLink
          >
        </footer>
      </template>
    </article>
  </main>
</template>

<script setup lang="ts">
import type { SharedDocument } from '@/api/documentShares'

import { computed } from 'vue'

import MarkdownView from '@/components/common/MarkdownView.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'
import { absDateTime } from '@/lib/dateText'

const props = defineProps<{
  /** 正文里的图片地址已经换成分享页能取的那一份；还在读或读不到时是 null。 */
  doc: SharedDocument | null
  /** 链接失效或读不到时代替正文的那一句。 */
  error: string
}>()

const updated = computed(() => (props.doc ? absDateTime(Date.parse(props.doc.updated_at)) : ''))

const sharerName = computed(() => {
  const by = props.doc?.shared_by
  if (!by) return ''
  return by.nickname || `@${by.handle}`
})

const hasInteractiveApp = computed(() => /(?:^|\n):::app\b/.test(props.doc?.content ?? ''))

const colophonText = computed(() => {
  if (!sharerName.value) return t('publicSite.sharedDoc.colophon.brand')
  return hasInteractiveApp.value
    ? t('publicSite.sharedDoc.colophon.byWithApp', { name: sharerName.value })
    : t('publicSite.sharedDoc.colophon.by', { name: sharerName.value })
})

/** The way in on the sharer's own invite code, while it still has seats; otherwise none. */
const invite = computed(() => {
  const by = props.doc?.shared_by
  if (!by?.code || by.seats_left <= 0) return null
  return {
    to: { path: '/account/signup', query: { code: by.code } },
  }
})
</script>

<style scoped>
.shared {
  min-height: 100vh;
  background: var(--surface);
  color: var(--text);
  padding: 40px 16px 64px;
}

.shared-column {
  max-width: var(--page-w-read);
  margin: 0 auto;
}

.shared-title {
  margin-bottom: 8px;
}

.shared-meta {
  margin-bottom: 24px;
}

/* A colophon, not a banner: one quiet line under the text, the way in on the right. */
.shared-colophon {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  align-items: baseline;
  justify-content: space-between;
  margin-top: 64px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.shared-colophon__link {
  color: var(--muted);
}

.shared-colophon__link:hover {
  color: var(--text);
}

.shared-gone {
  padding-top: 64px;
  text-align: center;
}
</style>
