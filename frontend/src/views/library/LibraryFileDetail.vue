<script setup lang="ts">
// 资料库里打开的那一份：谁在哪给的、被引用和替换过几次、里面写了什么。桌面上是
// 列表右边一栏，手机上是整一页（底下一颗「下载」）。
//
// 字节怎么读由页面给（`read`）：这一栏只摆，不知道服务端在哪。
import type { LibraryFile, LibraryShelf } from '@/lib/libraryApi'

import { computed, ref } from 'vue'

import { usePaneWidth } from '@/composables/usePaneWidth'

import BaseButton from '@/components/base/BaseButton.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import NavLink from '@/components/common/NavLink.vue'
import PaneHandle from '@/components/common/PaneHandle.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { PREVIEW_MAX_MB, tooLargeToPreview } from '@/lib/fileKind'
import { relTime } from '@/lib/relTime'
import { topicTitle } from '@/lib/topicState'

const props = defineProps<{
  /** 哪一个资料库：项目的资料库才有「在哪个频道给的」。 */
  shelf: LibraryShelf
  file: LibraryFile
  /** 桌面（右边一栏）还是手机（整一页）。 */
  wide: boolean
  /** 同一个名字被替换以后加一，预览跟着重读。 */
  revision: number
  /** 这一份正在替换。 */
  busy: boolean
  read: (asPdf: boolean) => Promise<ArrayBuffer>
}>()

const emit = defineEmits<{ download: []; replace: []; close: [] }>()

const tooLarge = computed(() => tooLargeToPreview(props.file.bytes))

// 桌面上这一栏可以往左拖宽，记在这台浏览器上（项目和空间的资料库共用这一份）。
const root = ref<HTMLElement | null>(null)
const pane = usePaneWidth('cheese.library.previewShare', root)
</script>

<template>
  <section
    ref="root"
    class="library-detail"
    :class="{ 'library-detail--phone': !props.wide }"
    :style="props.wide ? pane.style.value : undefined"
  >
    <PaneHandle v-if="props.wide" :pane="pane" />
    <header class="library-detail__head">
      <h2 v-if="props.wide" class="t-title library-detail__name">{{ props.file.path }}</h2>
      <p class="t-meta c-faint library-detail__meta">
        {{ props.file.added_by ?? t('work.library.unknownSource') }} · {{ relTime(props.file.added_at) }} ·
        {{ fmtBytes(props.file.bytes) }}
        <template v-if="props.file.room && typeof props.shelf === 'string'">
          · {{ t('work.library.from') }}
          <NavLink :to="{ name: 'workspace-topic', params: { projectId: props.shelf, topicId: props.file.room.id } }">{{
            t('work.topic.quoted', { title: topicTitle(props.file.room) })
          }}</NavLink>
        </template>
      </p>
      <p v-if="props.file.references || props.file.replaced" class="t-meta c-faint library-detail__meta">
        <template v-if="props.file.references">{{
          t('work.library.references', { n: props.file.references })
        }}</template>
        <template v-if="props.file.references && props.file.replaced"> · </template>
        <template v-if="props.file.replaced">{{
          t('work.library.replacedCount', { n: props.file.replaced })
        }}</template>
      </p>
      <div v-if="props.wide" class="library-detail__actions">
        <BaseButton kind="primary" size="sm" prepend-icon="mdi-download-outline" @click="emit('download')">
          {{ t('work.library.download') }}
        </BaseButton>
        <BaseButton
          v-if="!props.file.readonly"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-file-replace-outline"
          :loading="props.busy"
          @click="emit('replace')"
        >
          {{ t('work.library.replace') }}
        </BaseButton>
        <BaseButton size="sm" prepend-icon="mdi-close" :aria-label="t('work.library.close')" @click="emit('close')">
          {{ t('work.library.close') }}
        </BaseButton>
      </div>
    </header>
    <div class="library-detail__preview">
      <!-- The download is the one already on the page: the header's on desktop, the bottom bar's on a phone. -->
      <p v-if="tooLarge" class="library-detail__large t-body c-muted">
        {{ t('work.library.tooLargeToPreview', { mb: PREVIEW_MAX_MB }) }}
      </p>
      <FileBytesPreview
        v-else
        :filename="props.file.path"
        :source="`${props.file.path}#${props.revision}`"
        :read="props.read"
      />
    </div>
    <div v-if="!props.wide" class="library-detail__bar">
      <BaseButton kind="primary" block @click="emit('download')">{{ t('work.library.download') }}</BaseButton>
    </div>
  </section>
</template>

<style scoped>
.library-detail {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  width: 50%;
  min-width: min(480px, 100%);
  min-height: 0;
  border-left: 1px solid var(--line);
}

.library-detail--phone {
  flex: 1 1 auto;
  width: auto;
  border-left: 0;
}

.library-detail__head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--line);
}

.library-detail--phone .library-detail__head {
  padding: 10px 16px;
}

.library-detail__name {
  margin: 0;
  overflow-wrap: anywhere;
}

.library-detail__meta {
  margin: 0;
  overflow-wrap: anywhere;
}

.library-detail__meta a {
  color: var(--accent-ink);
  text-decoration: none;
}

.library-detail__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0 0 -8px;
}

.library-detail__large {
  margin: 0;
  padding: 24px;
  text-align: center;
}

.library-detail__preview {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.library-detail__bar {
  flex: none;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
}
</style>
