<script setup lang="ts">
// 当前预览里「一个跑着的东西」那一档：应用条、运行时状态、frames，以及应用那一档的
// 框选遮罩。
//
// 两种帧同住一层：有地址的网页（`frames`，一次挂两帧，切的时候旧的留着不重挂，见
// 宿主那一层）和只有名字的应用（`frameName`，授权表投进去的那一个）。哪一帧在台前由
// `displayedFrame` 说，其余几帧 `inert` 收起来——它们还在跑，只是不接读者的输入。
import type { PreviewFrame, PreviewNavigation } from '@/composables/usePreviewFrames'
import type { FileContent } from '@/cx_types'
import type { WebRect, WebViewport } from '@/lib/quotedContext'

import { openPreviewInNewTab } from './previewNewTab'
import PreviewPickToggle from './PreviewPickToggle.vue'
import PreviewRegionPick from './PreviewRegionPick.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { MCP_APP_MIME, MCP_APP_SANDBOX } from '@/lib/mcpAppBridge'

const props = defineProps<{
  topicId: string | null
  /** 这一格看的是房间里指定的哪一份文件；不给就是跟着当前预览走的那一格。
   *
   *  指定了文件的那一格不跟着当前预览走，所以顶栏那些动作（发布、在新标签页打开）都不
   *  给它——这一份自己的两条（在新标签页打开、下载）留在帧的预览条上，和文档条上那两条
   *  一样。 */
  path?: string | null
  frameName: string
  frames?: PreviewFrame[]
  displayedFrame?: PreviewFrame | null
  navigation?: PreviewNavigation
  navigationError?: string
  previewFile: FileContent | null
  previewMime: string
  previewUrl: string | null
  previewAppNote: string
  previewTunnelUp: boolean
  previewError: string | null
  previewReadError: string | null
  /** 圈选开关开着没有，以及它在哪一档上（判据在 `usePreviewPick`）。
   *
   *  `region` 那一档（应用、没有桥、读不到页面）由宿主在 iframe 上盖一层透明遮罩拖矩形，
   *  见 `PreviewRegionPick`。 */
  pickTarget: 'runtime' | 'region' | null
  pickOn: boolean
  /** 这间房的队友叫什么：连不上时那句话请它去查。 */
  agentName: string
}>()

const emit = defineEmits<{
  refresh: []
  'frame-load': [id: number, event: Event]
  'frame-error': [id: number, event: Event]
  'toggle-pick': []
  'pick-region': [payload: { rect: WebRect; viewport: WebViewport }]
  download: []
}>()

// 房间自己的 artifact 与应用共用一套沙箱：它们从内容域发出来，所以带着
// `allow-same-origin`。第三方 MCP View 不能带 —— `allow-scripts` 加
// `allow-same-origin` 会把手写这份 HTML 的人放进内容域的存储里。同一个面板、
// 同一个预览源，沙箱按这一帧的 mime 分：房内那套不变，第三方那套收紧。
const DEFAULT_SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox'
function sandboxFor(mime?: string | null): string {
  return mime === MCP_APP_MIME ? MCP_APP_SANDBOX : DEFAULT_SANDBOX
}
</script>

<template>
  <div class="preview-wrap">
    <div class="preview-bar text-caption px-3 pt-2">
      <span class="text-medium-emphasis">{{ displayedFrame?.label || previewAppNote || previewFile?.path }}</span>
      <span v-if="displayedFrame?.version" class="text-medium-emphasis ms-2">{{
        t('work.room.preview.readVersion', { version: displayedFrame.version })
      }}</span>
      <v-chip
        v-if="
          displayedFrame
            ? displayedFrame.live && displayedFrame.connection === 'online' && !navigationError
            : previewAppNote
        "
        size="x-small"
        variant="tonal"
        class="ms-2"
        >{{ t('work.room.preview.runningApp') }}</v-chip
      >
      <v-chip v-else size="x-small" variant="outlined" class="ms-2">{{ displayedFrame?.mime || previewMime }}</v-chip>
      <template v-if="path">
        <v-spacer />
        <BaseButton
          kind="ghost"
          icon="mdi-open-in-new"
          size="sm"
          :title="t(displayedFrame?.live ? 'work.room.preview.openLatestPreview' : 'work.room.preview.openInNewTab')"
          @click="openPreviewInNewTab(props.topicId, props.displayedFrame, path)"
        />
        <BaseButton
          kind="ghost"
          icon="mdi-download"
          size="sm"
          :title="t('work.room.preview.download')"
          @click="emit('download')"
        />
      </template>
      <PreviewPickToggle v-if="pickTarget" :on="pickOn" @toggle="emit('toggle-pick')" />
    </div>
    <div v-if="displayedFrame" role="status" class="preview-runtime-status px-3 py-2 text-caption">
      <span v-if="displayedFrame.resourceId" :title="displayedFrame.resourceId">{{
        t('work.room.preview.resourceIdentity', { id: displayedFrame.resourceId.slice(0, 12) })
      }}</span>
      <span v-if="displayedFrame.instance">
        · {{ t('work.room.preview.instanceIdentity', { id: displayedFrame.instance.slice(0, 12) }) }}</span
      >
      <span v-else>
        · {{ t(displayedFrame.live ? 'work.room.preview.mutableLive' : 'work.room.preview.mutableFile') }}</span
      >
      <span>
        ·
        {{
          t(
            displayedFrame.runtime === 'ready'
              ? 'work.room.preview.runtimeReady'
              : displayedFrame.runtime === 'failed'
                ? 'work.room.preview.runtimeFailed'
                : 'work.room.preview.runtimeUnconfirmed'
          )
        }}</span
      >
      <span v-if="displayedFrame.connection === 'disconnected'">
        · {{ t('work.room.preview.instanceDisconnected') }}</span
      >
      <span v-if="displayedFrame.connection === 'gone'"> · {{ t('work.room.preview.instanceGone') }}</span>
      <div v-if="displayedFrame.runtimeError" role="alert">{{ displayedFrame.runtimeError }}</div>
    </div>
    <div
      v-if="navigation === 'authorizing' || navigation === 'navigating'"
      role="status"
      class="px-3 py-2 text-caption"
    >
      {{ t(navigation === 'authorizing' ? 'work.room.preview.authorizing' : 'work.room.preview.navigating') }}
    </div>
    <div v-if="navigationError || previewError || previewReadError" role="alert" class="px-3 py-2 text-error">
      {{ navigationError || previewError || previewReadError }}
      <div v-if="previewAppNote && !previewUrl">
        {{
          t(previewTunnelUp ? 'tasks.preview.appUnavailable' : 'tasks.preview.connectionUnavailable', {
            agent: agentName,
          })
        }}
      </div>
      <span v-if="displayedFrame">{{ t('work.room.preview.retainedPage') }}</span>
      <BaseButton kind="secondary" size="sm" @click="emit('refresh')">{{
        t('work.room.preview.retryTarget')
      }}</BaseButton>
    </div>
    <BaseButton v-if="path" kind="ghost" size="sm" :title="t('work.room.preview.refresh')" @click="emit('refresh')">{{
      t('work.room.preview.refresh')
    }}</BaseButton>
    <!-- Authorization still POSTs only to named sandboxed content-domain frames. -->
    <div class="preview-frames">
      <template v-if="frames">
        <iframe
          v-for="frame in frames"
          :key="frame.id"
          :name="frame.name"
          class="preview-frame"
          :class="{ 'preview-frame--incoming': frame.id !== displayedFrame?.id }"
          :inert="frame.id !== displayedFrame?.id"
          :aria-hidden="frame.id !== displayedFrame?.id"
          :tabindex="frame.id === displayedFrame?.id ? 0 : -1"
          :title="t('work.room.preview.frameTitle')"
          :sandbox="sandboxFor(frame.mime)"
          @load="emit('frame-load', frame.id, $event)"
          @error="emit('frame-error', frame.id, $event)"
        />
      </template>
      <iframe
        v-else
        :name="frameName"
        class="preview-frame"
        :title="t('work.room.preview.frameTitle')"
        :sandbox="DEFAULT_SANDBOX"
      />
      <PreviewRegionPick v-if="pickTarget === 'region' && pickOn" @pick="emit('pick-region', $event)" />
    </div>
  </div>
</template>

<style scoped>
/* 填满剩下的空间，但**不许被挤没**：flex-shrink 是 0，不是 1。缩到 0 时内容不会
   跟着消失，应用条和 iframe 会溢出去盖住同一列里的别的块（定位条、编辑器）。
   shrink 归零之后高度由内容决定（应用条 + 预览自己的 240px 地板），再长就整块
   面板一起滚，谁也不盖谁。 */
.preview-wrap {
  flex: 1 0 auto;
  display: flex;
  flex-direction: column;
}
.preview-bar {
  display: flex;
  align-items: center;
}
.preview-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  min-height: var(--preview-min);
  /* Theme-invariant on purpose: the iframe renders arbitrary user HTML that
     assumes a white page (its own text is near-black). Painting the backing
     dark would leave black text on a dark ground wherever that document is
     transparent — the page controls its own colours, we only back it. */
  /* stylelint-disable-next-line color-no-hex -- see the reason above */
  background: #fff;
}
.preview-runtime-status {
  overflow-wrap: anywhere;
}
.preview-frames {
  position: relative;
  flex: 1 1 auto;
  min-height: var(--preview-min);
}
.preview-frames .preview-frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.preview-frame--incoming {
  opacity: 0;
  pointer-events: none;
}
</style>
