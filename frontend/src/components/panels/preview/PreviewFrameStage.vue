<script setup lang="ts">
// 当前预览里「一个跑着的东西」那一档：应用条、运行时状态、frames，以及应用那一档的
// 框选遮罩。
//
// 条的上半是「小浏览器」那一行（后退、前进、刷新、地址栏、连通链，见 `PreviewBrowserBar`）；
// 应用睡着时帧上盖一层静态快照和唤醒卡（见 `PreviewAppSleep`）。
//
// 两种帧同住一层：有地址的网页（`frames`，一次挂两帧，切的时候旧的留着不重挂，见
// 宿主那一层）和只有名字的应用（`frameName`，授权表投进去的那一个）。哪一帧在台前由
// `displayedFrame` 说，其余几帧 `inert` 收起来——它们还在跑，只是不接读者的输入。
import type { AppWakeStep } from '@/composables/panelPreview/appWake'
import type { PreviewFrame, PreviewNavigation } from '@/composables/usePreviewFrames'
import type { FileContent } from '@/cx_types'
import type { WebRect, WebViewport } from '@/lib/quotedContext'

import { computed, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'

import PreviewAppSleep from './PreviewAppSleep.vue'
import PreviewBrowserBar from './PreviewBrowserBar.vue'
import { openPreviewInNewTab } from './previewNewTab'
import PreviewPickToggle from './PreviewPickToggle.vue'
import PreviewRegionPick from './PreviewRegionPick.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { MCP_APP_MIME, MCP_APP_SANDBOX } from '@/lib/mcpAppBridge'
import { appAsleep, previewChain } from '@/lib/previewChain'
import { previewStage } from '@/lib/previewStage'

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
  /** 应用的端口有应答（后端给了地址）：连通链「应用」那一段。 */
  previewAppUp: boolean
  /** 这个应用最近一次成功加载时留的静态快照，和它是多久以前留的。 */
  appSnapshot?: { html: string; age: string } | null
  /** 唤醒的分步进度（没在唤醒是 null）和没成时那一句。 */
  wakeSteps?: AppWakeStep[] | null
  wakeError?: string
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
  /** 后退、前进、重载帧里那一页（由帧里的桥执行）。 */
  history: [action: 'back' | 'forward' | 'reload']
  /** 地址栏里敲了一个地址。 */
  navigate: [path: string]
  wake: []
}>()

// 房间自己的 artifact 与应用共用一套沙箱：它们从内容域发出来，所以带着
// `allow-same-origin`。第三方 MCP View 不能带 —— `allow-scripts` 加
// `allow-same-origin` 会把手写这份 HTML 的人放进内容域的存储里。同一个面板、
// 同一个预览源，沙箱按这一帧的 mime 分：房内那套不变，第三方那套收紧。
const DEFAULT_SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox'
function sandboxFor(mime?: string | null): string {
  return mime === MCP_APP_MIME ? MCP_APP_SANDBOX : DEFAULT_SANDBOX
}

// 导航走到哪一步、屏幕上有没有一帧可看，交给 `lib/previewStage` 一处判（它和文档那一档
// 共用这一套名词）。这一档的两句文案照旧分「正在获取授权」和「正在加载新页面」——它们对应
// 状态机里的 `auth` 和 `navigate`。
//
// 这一档和文档那一档的差别也在这里：换一页时上一帧留在屏幕上（新导航不清掉已加载的那一
// 帧），所以「有帧可看」和「正在加载」会同时成立。状态机把这两件事分开说，正是为此——
// 压成互斥的一个值，这行「正在加载新页面」除了第一次导航就再也说不出口了。
const stage = computed(() =>
  previewStage({
    loading: props.navigation === 'authorizing' ? 'auth' : props.navigation === 'navigating' ? 'navigate' : null,
    shown: !!props.displayedFrame,
    // 这一档的「上一版还在」由运行时那几行字自己说（实例断了、运行时失败都在那一条里），
    // 状态机的 stale 是给文档那一档的「这版没取成」用的，这里不重复。
    stale: false,
    rendererMissing: false,
    cannotRender: false,
  })
)

// 此刻在取哪一步（`null`＝没在取）。两句文案就在这一处说出来。
const step = computed(() => {
  const now = stage.value
  if (now.stage === 'shown') return now.loading
  return now.stage === 'waiting' ? now.step : null
})

const showSource = ref(false)
const viewport = ref<'desktop' | 'tablet' | 'mobile'>('desktop')

// 「窄」按这一格自己的宽度算，不看窗口：预览在右栏里可以是 466px，或者手机上整页的
// 一格里——那两种都得把这一条收成一行。还没量到宽度（挂载前、测试里没有布局）按宽处
// 理，先按平铺画，量到了再收，免得一上来就少一排按钮。
const wrapRef = ref<HTMLElement | null>(null)
const { width: wrapWidth } = useElementSize(wrapRef)
const compact = computed(() => wrapWidth.value > 0 && wrapWidth.value < 560)

// 运行状态平时不占一行：连通链在地址栏旁边，三段都通就是三个绿勾（窄时一颗绿点）；
// 哪一段不通那段变灰，点开看原因。原来那颗「出问题才亮」的圆点就是这条链（见
// `lib/previewChain`）。
const live = computed(() => !!props.displayedFrame?.live || !!props.previewAppNote)
const segments = computed(() =>
  previewChain({
    platformError: props.previewError,
    live: live.value,
    tunnelUp: props.previewTunnelUp,
    appAnswering: props.previewAppUp,
    frame: props.displayedFrame ?? null,
    navigationError: props.navigationError ?? '',
    agentName: props.agentName,
  })
)
/** 应用睡着了（电脑休眠、或电脑在应用没答）：帧上盖快照和唤醒卡。正在唤醒时也算——
 *  唤醒卡上的进度要一直摆到新页面装上为止。 */
const asleep = computed(() => (live.value ? appAsleep(segments.value) : null))
const sleepKind = computed(() => asleep.value ?? (props.wakeSteps ? 'machine' : null))

/** 链点开后最下面那几行：资源、实例、读到的版本、就绪与否。平时不读，要查时还在。 */
const details = computed(() => {
  const frame = props.displayedFrame
  if (!frame) return []
  const parts: string[] = []
  if (frame.version) parts.push(t('work.room.preview.readVersion', { version: frame.version }))
  if (frame.resourceId) parts.push(t('work.room.preview.resourceIdentity', { id: frame.resourceId.slice(0, 12) }))
  if (frame.instance) parts.push(t('work.room.preview.instanceIdentity', { id: frame.instance.slice(0, 12) }))
  else parts.push(t(frame.live ? 'work.room.preview.mutableLive' : 'work.room.preview.mutableFile'))
  parts.push(
    t(
      frame.runtime === 'ready'
        ? 'work.room.preview.runtimeReady'
        : frame.runtime === 'failed'
          ? 'work.room.preview.runtimeFailed'
          : 'work.room.preview.runtimeUnconfirmed'
    )
  )
  return parts
})

/** 刷新：帧里有桥就让那一页自己重载（表单、登录态都还在这个源里）；没有桥就整帧重来。 */
function reload() {
  if (props.displayedFrame?.bridged && !asleep.value) emit('history', 'reload')
  else emit('refresh')
}

function openTab() {
  openPreviewInNewTab(props.topicId, props.displayedFrame, props.path)
}
const canShowSource = computed(
  () =>
    !props.previewAppNote &&
    !props.displayedFrame?.live &&
    props.previewFile?.content !== null &&
    !!props.previewFile?.content
)

/** 条上那几件动作：宽时平铺成 BaseButton，窄时收进「…」里的一行。一处写、两处画，
 *  两边不会再各写一遍同一组图标和文案（那正是它们以前对不上的原因）。 */
type BarAction = {
  key: string
  icon: string
  label: string
  title?: string
  /** 平铺时只画图标（条上这几颗都是）：菜单里总是图标加字。 */
  iconOnly?: boolean
  /** 平铺那颗按钮的 data-testid（窄时的菜单行不挂它）。 */
  testid?: string
  disabled?: boolean
  /** 菜单里那一行是不是当前选中的那一档（视口三档）；不是单选的那几行不给。 */
  active?: boolean
  run: () => void
}
const VIEWPORTS = [
  { key: 'desktop', icon: 'mdi-monitor', label: 'work.room.preview.viewportDesktop' },
  { key: 'tablet', icon: 'mdi-tablet', label: 'work.room.preview.viewportTablet' },
  { key: 'mobile', icon: 'mdi-cellphone', label: 'work.room.preview.viewportMobile' },
] as const
const actions = computed<BarAction[]>(() => {
  const list: BarAction[] = []
  if (props.path) {
    list.push({
      key: 'open',
      icon: 'mdi-open-in-new',
      label: t(props.displayedFrame?.live ? 'work.room.preview.openLatestPreview' : 'work.room.preview.openInNewTab'),
      iconOnly: true,
      run: openTab,
    })
    list.push({
      key: 'download',
      icon: 'mdi-download',
      label: t('work.room.preview.download'),
      iconOnly: true,
      run: () => emit('download'),
    })
  }
  if (canShowSource.value) {
    list.push({
      key: 'source',
      icon: showSource.value ? 'mdi-web' : 'mdi-code-tags',
      label: t(showSource.value ? 'work.room.preview.webView' : 'work.room.preview.showSource'),
      testid: 'toggle-frame-source',
      run: () => {
        showSource.value = !showSource.value
      },
    })
  }
  // 窄时条上放不下视口三键，它们进菜单，照样能切。
  if (compact.value && !showSource.value) {
    for (const mode of VIEWPORTS) {
      list.push({
        key: `viewport-${mode.key}`,
        icon: mode.icon,
        label: t(mode.label),
        active: viewport.value === mode.key,
        run: () => {
          viewport.value = mode.key
        },
      })
    }
  }
  return list
})

watch(
  () => props.previewFile?.path,
  () => {
    showSource.value = false
    viewport.value = 'desktop'
  }
)
</script>

<template>
  <div ref="wrapRef" class="preview-wrap">
    <div class="preview-bar text-caption px-2 pt-1">
      <PreviewBrowserBar
        :href="displayedFrame?.href ?? ''"
        :fallback="displayedFrame?.label || previewAppNote || previewFile?.path || ''"
        :can-back="!asleep && !!displayedFrame?.canBack"
        :can-forward="!asleep && !!displayedFrame?.canForward"
        :navigable="!asleep && !!displayedFrame?.bridged"
        :segments="segments"
        :details="details"
        :compact="compact"
        @back="emit('history', 'back')"
        @forward="emit('history', 'forward')"
        @reload="reload"
        @navigate="emit('navigate', $event)"
      />
      <template v-if="compact">
        <v-menu v-if="actions.length" location="bottom end">
          <template #activator="{ props: menuProps }">
            <BaseButton
              v-bind="menuProps"
              kind="ghost"
              icon="mdi-dots-horizontal"
              size="sm"
              density="comfortable"
              data-testid="preview-more"
              :title="t('work.room.menu.more')"
              :aria-label="t('work.room.menu.more')"
            />
          </template>
          <v-list density="compact" role="menu">
            <v-list-item
              v-for="action in actions"
              :key="action.key"
              :role="action.active === undefined ? 'menuitem' : 'menuitemradio'"
              :aria-checked="action.active"
              :prepend-icon="action.icon"
              :active="action.active"
              :disabled="action.disabled"
              @click="action.run()"
            >
              <v-list-item-title>{{ action.label }}</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </template>
      <template v-else>
        <BaseButton
          v-for="action in actions"
          :key="action.key"
          kind="ghost"
          size="sm"
          :icon="action.iconOnly ? action.icon : undefined"
          :prepend-icon="action.iconOnly ? undefined : action.icon"
          :disabled="action.disabled"
          :data-testid="action.testid"
          :title="action.title ?? action.label"
          @click="action.run()"
        >
          <template v-if="!action.iconOnly">{{ action.label }}</template>
        </BaseButton>
        <div v-if="!showSource" class="preview-viewport ms-2">
          <button
            v-for="mode in VIEWPORTS"
            :key="mode.key"
            type="button"
            class="preview-viewport__btn"
            :class="{ 'preview-viewport__btn--active': viewport === mode.key }"
            :data-testid="`viewport-${mode.key}`"
            :aria-pressed="viewport === mode.key"
            :title="t(mode.label)"
            :aria-label="t(mode.label)"
            @click="viewport = mode.key"
          >
            <v-icon size="15">{{ mode.icon }}</v-icon>
          </button>
        </div>
      </template>
      <PreviewPickToggle v-if="pickTarget && !showSource" :on="pickOn" @toggle="emit('toggle-pick')" />
    </div>
    <div v-if="step" role="status" class="px-3 py-2 text-caption">
      {{ t(step === 'auth' ? 'work.room.preview.authorizing' : 'work.room.preview.navigating') }}
    </div>
    <div
      v-if="!sleepKind && (navigationError || previewError || previewReadError)"
      role="alert"
      class="px-3 py-2 text-error"
    >
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
    <!-- Authorization still POSTs only to named sandboxed content-domain frames. -->
    <pre v-if="showSource && canShowSource" class="preview-source" data-testid="frame-source">{{
      previewFile?.content ?? ''
    }}</pre>
    <div
      v-show="!(showSource && canShowSource)"
      :data-viewport="viewport"
      :class="[
        'preview-frames',
        `preview-frames--${viewport}`,
        { 'preview-frames--asleep': sleepKind && !appSnapshot },
        // 快照盖在上面时，下面那一帧留着不重挂，但不该再接点击和键盘：读者看到的、
        // 能操作的是快照。旧页面被点一下就会走进错误页，从半透明的快照底下透出来。
        { 'preview-frames--covered': !!sleepKind && !!appSnapshot },
      ]"
    >
      <template v-if="frames">
        <iframe
          v-for="frame in frames"
          :key="frame.id"
          :name="frame.name"
          class="preview-frame"
          :class="{ 'preview-frame--incoming': frame.id !== displayedFrame?.id }"
          :inert="!!sleepKind || frame.id !== displayedFrame?.id"
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
      <PreviewAppSleep
        v-if="sleepKind"
        :kind="sleepKind"
        :snapshot-html="appSnapshot?.html ?? null"
        :snapshot-age="appSnapshot?.age ?? ''"
        :bare="!appSnapshot && !displayedFrame"
        :steps="wakeSteps ?? null"
        :wake-error="wakeError ?? ''"
        :picking="pickOn"
        :agent-name="agentName"
        @wake="emit('wake')"
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
  flex-wrap: nowrap;
  min-width: 0;
}
.preview-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 只有文件名让位：条上其余几件都别被挤变形。 */
.preview-bar__name {
  flex: 0 1 auto;
  min-width: 0;
}
.preview-viewport {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
button.preview-viewport__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 22px;
  padding: 0;
  color: var(--muted);
  border-radius: var(--radius-sm);
}
button.preview-viewport__btn:hover {
  color: var(--text);
  background: var(--fill-2);
}
button.preview-viewport__btn.preview-viewport__btn--active {
  color: var(--accent-ink);
  background: var(--accent-wash);
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
/* 应用睡着、又没有快照可顶时，屏幕上那张旧页面压暗、不收鼠标：它还是最后看到的样子，
   但已经点不动了。 */
.preview-frames--asleep .preview-frame {
  filter: grayscale(1);
  opacity: 0.55;
  pointer-events: none;
}
/* 快照盖在上面：下面那一帧已经死了，留着只是为了不重挂；不该再看得见、点得到。 */
.preview-frames--covered .preview-frame {
  visibility: hidden;
}
.preview-frames {
  position: relative;
  flex: 1 1 auto;
  width: 100%;
  min-height: var(--preview-min);
}
.preview-frames--tablet {
  width: min(100%, 768px);
  margin-inline: auto;
}
.preview-frames--mobile {
  width: min(100%, 375px);
  margin-inline: auto;
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
.preview-source {
  flex: 1 1 auto;
  min-height: var(--preview-min);
  margin: 0;
  padding: 12px 16px 24px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
