<script setup lang="ts">
// 一条消息里的视频，或者右侧预览面板里的那一档。
//
// 画成一张卡：上面是画面，下面是自绘的控制条，再下面一行是这个文件的身份（名字、
// 时长、大小）和它能做的事（在右侧打开、下载、批注当前时间点）。
//
// 为什么不用浏览器自带的控制条：原型上的进度条要挂批注标记（第几秒上有人说过话），
// 原生控件里塞不进任何东西；原生控件在不同系统上还长得不一样，一个产品两副面孔。
// 代价是键盘、读屏、全屏这些原本白拿的东西要自己接：进度条用真的 `<input type=range>`
// （读屏念得出、键盘走得动），画面本身可聚焦、可回车播放，快捷键只在这一张卡拿到焦点
// 时才生效（见下面 `onKeydown`）。
//
// 字节仍然只在读者要的时候取：一条长对话里几十段录屏，谁也不该为它们先下一遍。
// 打开之后也只要元数据（`preload="metadata"`），播放器自己按 range 请求流式读。
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'

import BaseButton from './base/BaseButton.vue'

import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { fmtFileSize } from '@/lib/usageFormat'
import { connectionInfo, formatVideoTime, nextVideoRate, prefersLightVideo } from '@/lib/videoPlayback'

const props = defineProps<{
  topicId: string | null
  path: string
  canOpen?: boolean
  canLocate?: boolean
  /** 摆在右侧预览面板里（`panel`）还是一段对话里（默认）。面板那一档更宽、更高。 */
  variant?: 'card' | 'panel'
}>()
const emit = defineEmits<{ open: []; download: []; locate: [payload: { seconds: number; timestamp: string }] }>()
const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const name = computed(() => props.path.split('/').pop() ?? props.path)
const url = ref('')
const videoRef = ref<HTMLVideoElement | null>(null)
// width / height of the recording, once the player has read it. Until then the
// box is 16:9; after it, a portrait phone recording gets a tall box instead of
// being letterboxed into a wide one.
const ratio = ref<number | null>(null)
const duration = ref(0)
const current = ref(0)
/** 整段录屏多少字节，从签发地址那次响应的 Content-Range 上读来；读不到就不写。 */
const bytes = ref<number | null>(null)
const playing = ref(false)
const muted = ref(false)
const rate = ref(1)
const loading = ref(false)
/** 元数据回来了、画面还没出第一帧：转圈，别让读者对着黑框猜。 */
const starting = ref(false)
/** 起播帧：真实的第一帧，画不出来就退回类型图标（见 `capture`）。 */
const poster = ref('')
const failure = ref<'load' | 'play' | 'offline' | null>(null)
let generation = 0

const panel = computed(() => props.variant === 'panel')
// 触屏上把控制权交还给系统播放器：全屏、画中画、AirPlay、手势拖动进度都是它给的，
// 自绘一条做不到这些。桌面（精确指针）才用自绘那一条——那里有键盘，也才挂得住
// 之后要接的批注 pin。这是「手机原生还是自绘」的取舍落点。
const coarse = useMediaQuery('(pointer: coarse)')

function reset() {
  generation += 1
  url.value = ''
  ratio.value = null
  duration.value = 0
  current.value = 0
  bytes.value = null
  playing.value = false
  muted.value = false
  rate.value = 1
  loading.value = false
  starting.value = false
  failure.value = null
  releasePoster()
}

function releasePoster() {
  if (poster.value.startsWith('blob:')) URL.revokeObjectURL(poster.value)
  poster.value = ''
}

// Fetch only on request. The player streams from the URL it is given, so a long
// recording starts without being downloaded whole first.
async function load() {
  reset()
  const mine = generation
  if (!props.topicId) {
    failure.value = 'load'
    return
  }
  // 没网就别发那条注定失败的请求，直接说清是哪种「打不开」：读不到和放不了
  // 是两回事，没网又是第三回事，三种要读者做的事各不相同。
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    failure.value = 'offline'
    return
  }
  loading.value = true
  try {
    const next = await source.mediaUrl(props.topicId, props.path)
    if (mine !== generation) return
    url.value = next.url
    bytes.value = next.bytes
    starting.value = true
  } catch {
    if (mine === generation) failure.value = 'load'
  } finally {
    if (mine === generation) loading.value = false
  }
}

function measure(event: Event) {
  const video = event.target as HTMLVideoElement
  if (video.videoWidth > 0 && video.videoHeight > 0) ratio.value = video.videoWidth / video.videoHeight
  if (Number.isFinite(video.duration)) duration.value = video.duration
  // 元数据回来了就算「能玩了」：起播帧要等 loadeddata，但那一步在有些浏览器上
  // 要等读者真的按播放才来，不该让他先对着一个转圈猜。
  starting.value = false
}

/**
 * 起播帧：把播放器停在 0.1 秒，把那一帧画进画布。
 *
 * 卡上先有一张真的起播帧，读者一眼知道这段录屏讲的是哪一步；`preload=metadata`
 * 本来只拿到元数据，这一帧是自己画出来的。容器认得出不代表浏览器解得出它的编码
 * （`.mov` 在部分浏览器上放不出来，见 `videoMimeOf` 的注释）——画不出来就静默退回，
 * 播放本身不受影响。
 */
function capture() {
  const video = videoRef.value
  if (!video || poster.value) return
  const target = Number.isFinite(video.duration) && video.duration > 0.2 ? 0.1 : 0
  if (target === 0) {
    drawFrame(video)
    return
  }
  const seeked = () => {
    video.removeEventListener('seeked', seeked)
    drawFrame(video)
    video.currentTime = 0
  }
  video.addEventListener('seeked', seeked)
  try {
    video.currentTime = target
  } catch {
    video.removeEventListener('seeked', seeked)
  }
}

function drawFrame(video: HTMLVideoElement) {
  try {
    if (!video.videoWidth || !video.videoHeight) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const context = canvas.getContext('2d')
    if (!context) return
    context.drawImage(video, 0, 0)
    canvas.toBlob(
      (blob) => {
        if (blob) poster.value = URL.createObjectURL(blob)
      },
      'image/jpeg',
      0.8
    )
  } catch {
    // 画不出来就算了：卡上那一块退回类型图标，播放本身不受影响。
  }
}

/** 解码失败：收起播放器，换成那句话 + 下载入口。起播帧的转圈也在这一步一起停。 */
function onMediaError() {
  starting.value = false
  failure.value = 'play'
}

function onLoadedData() {
  starting.value = false
  // 省流或 2G 网：起播帧也省掉，读者真按播放时再出画面。
  if (!prefersLightVideo(connectionInfo())) capture()
}

function toggle() {
  const video = videoRef.value
  if (!video) return
  if (video.paused) void video.play().catch(() => (failure.value = 'play'))
  else video.pause()
}

/** 进度条上那一下：范围输入给的是秒数，直接落到播放位置上。 */
function seek(seconds: number) {
  const video = videoRef.value
  if (!video || !Number.isFinite(seconds)) return
  video.currentTime = Math.min(Math.max(seconds, 0), duration.value || seconds)
  current.value = video.currentTime
}

function onBar(event: Event) {
  seek(Number((event.target as HTMLInputElement).value))
}

function onTime() {
  const video = videoRef.value
  if (video) current.value = video.currentTime
}

function cycleRate() {
  rate.value = nextVideoRate(rate.value)
  const video = videoRef.value
  if (video) video.playbackRate = rate.value
}

function toggleMute() {
  muted.value = !muted.value
  const video = videoRef.value
  if (video) video.muted = muted.value
}

function nudgeVolume(delta: number) {
  const video = videoRef.value
  if (!video) return
  video.volume = Math.min(1, Math.max(0, video.volume + delta))
  if (video.volume > 0 && video.muted) {
    video.muted = false
    muted.value = false
  }
}

/**
 * 键盘：空格播放/暂停，左右键前后 5 秒，上下键音量。
 *
 * 只在这一张卡拿到焦点时才接：挂在 window 上的话，读者在输入框里敲空格会去暂停一段
 * 根本没在看的视频，方向键会把页面滚走。作用范围就是这一张卡——这是这一处「作用范围」
 * 的答案。
 */
function onKeydown(event: KeyboardEvent) {
  if (!url.value || failure.value === 'play') return
  // 卡片里的控件自己认键盘，别再替它们按一遍：进度条的方向键本来就在挪滑块（步长
  // 比 5 秒细得多），按钮的空格本来就会点下去。两处都接会互相抵消。
  const target = event.target as HTMLElement | null
  if (target?.tagName === 'INPUT') return
  if (event.key === ' ' && target?.tagName === 'BUTTON') return
  // 以播放器此刻真的位置为准，不用那个要等 timeupdate 才追上来的影子值。
  const at = () => videoRef.value?.currentTime ?? current.value
  const keys: Record<string, () => void> = {
    ' ': toggle,
    ArrowLeft: () => seek(at() - 5),
    ArrowRight: () => seek(at() + 5),
    ArrowUp: () => nudgeVolume(0.1),
    ArrowDown: () => nudgeVolume(-0.1),
  }
  const action = keys[event.key]
  if (!action) return
  event.preventDefault()
  action()
}

// 只有播放器在时才有「此刻」：视频还没打开（或打不开）时不该交出一个谁也没选过的 00:00。
function locateCurrentTime() {
  if (!videoRef.value) return
  const raw = videoRef.value.currentTime
  const seconds = Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0
  emit('locate', { seconds, timestamp: formatVideoTime(seconds) })
}

// 打不开是哪一种：读不到、放不了、没网，三种要读者做的事各不相同。
const failureText = computed(() => {
  if (failure.value === 'load') return t('work.room.video.loadFailed')
  if (failure.value === 'offline') return t('work.room.video.offline')
  if (failure.value === 'play') return t('work.room.video.playFailed')
  return ''
})

const locateHint = computed(() =>
  !url.value && !failure.value ? t('work.room.video.locateNeedsPlayer') : failure.value ? failureText.value : undefined
)

const meta = computed(() => {
  const parts = [name.value]
  if (duration.value > 0) parts.push(formatVideoTime(duration.value))
  if (bytes.value) parts.push(fmtFileSize(bytes.value))
  return parts.join(' · ')
})

watch(() => [props.topicId, props.path], reset)
onBeforeUnmount(() => {
  generation += 1
  releasePoster()
})
</script>

<template>
  <!-- 快捷键挂在这一张卡上（不是 window 上）：焦点在卡片里的某颗键或画面上时，空格播放/
       暂停、左右 ±5 秒、上下调音量才生效。焦点在输入框或页面别处时一个键都不碰。 -->
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the card is the keyboard scope itself: keydown here is the shortcut layer for the player inside, and every control in it is a real button or range with its own handlers -->
  <div class="video-card" :class="{ 'video-card--panel': panel }" tabindex="-1" @keydown="onKeydown">
    <div class="video-card__stage">
      <!-- 读者按过打开之后才挂播放器：一条长对话里几十段录屏，谁也不该为它们先下一遍。
           `preload="metadata"`：只要元数据（时长、画面尺寸），播放器自己按 range 请求读。 -->
      <!-- eslint-disable-next-line vuejs-accessibility/media-has-caption -- user-uploaded videos have no supplied caption track; the caption-free download stays available -->
      <video
        v-if="url && failure !== 'play'"
        ref="videoRef"
        class="video-card__video"
        :src="url"
        playsinline
        preload="metadata"
        :controls="coarse || undefined"
        :poster="poster || undefined"
        :style="ratio ? { '--video-ratio': ratio } : undefined"
        :aria-label="t('work.room.video.label', { name })"
        @loadedmetadata="measure"
        @loadeddata="onLoadedData"
        @timeupdate="onTime"
        @play="playing = true"
        @pause="playing = false"
        @error="onMediaError"
      />

      <!-- 画面上那一层是「点一下播放 / 点一下暂停」，铺满整个画面（和原生播放器一样）。
           它同时是这一格的焦点入口：按下去之后方向键、空格才落到这段视频上。
           播放器还没挂（还没打开）时它不出现，那时卡上那一块是打开入口本身。 -->
      <button
        v-if="url && failure !== 'play' && !coarse"
        type="button"
        class="video-card__hit"
        :aria-label="playing ? t('work.room.video.pause') : t('work.room.video.play')"
        data-testid="video-screen"
        @click="toggle"
      >
        <v-icon v-if="!playing" size="44" class="video-card__play">mdi-play-circle</v-icon>
      </button>

      <div v-if="starting && !poster" class="video-card__busy" role="status">
        <v-progress-circular indeterminate color="primary" size="24" />
      </div>

      <!-- 还没打开：画面上那一块就是「打开」本身——和原型一样，先给一块深色画面，
           中间一颗播放键，下面是这个名字。不开预览就不去取字节。 -->
      <button
        v-if="!url && !failure"
        type="button"
        class="video-card__hit video-card__hit--closed"
        :disabled="loading"
        :aria-label="t('work.room.video.open')"
        @click="load"
      >
        <v-progress-circular v-if="loading" indeterminate color="primary" size="24" />
        <template v-else>
          <v-icon size="44" class="video-card__play">mdi-play-circle-outline</v-icon>
          <span class="video-card__open-label">{{ t('work.room.video.open') }}</span>
        </template>
      </button>

      <!-- 打不开就说一句、给一条路：一块空白和「这条视频本来就是空的」长得一样。 -->
      <div v-if="failure" class="video-card__fallback">
        <p class="video-card__fail" role="status">{{ failureText }}</p>
        <BaseButton kind="ghost" :disabled="loading" prepend-icon="mdi-refresh" @click="load">
          {{ loading ? t('work.room.video.loading') : t('work.room.video.retry') }}
        </BaseButton>
      </div>
    </div>

    <!-- 控制条：只有播放器在时才有意义。进度条用真的 range，键盘走得动、读屏念得出。 -->
    <div v-if="url && failure !== 'play' && !coarse" class="video-card__ctl">
      <button
        type="button"
        class="video-card__key"
        :title="playing ? t('work.room.video.pause') : t('work.room.video.play')"
        :aria-label="playing ? t('work.room.video.pause') : t('work.room.video.play')"
        data-testid="video-play"
        @click="toggle"
      >
        <v-icon size="20">{{ playing ? 'mdi-pause' : 'mdi-play' }}</v-icon>
      </button>
      <!-- 面板那一档多两颗一秒钟的步进：长录屏里要停在某一帧上说话，拖进度条
           到不了那么准，原生控件里也没有这一步。 -->
      <template v-if="panel">
        <button
          type="button"
          class="video-card__key"
          :title="t('work.room.video.backSecond')"
          :aria-label="t('work.room.video.backSecond')"
          data-testid="video-back"
          @click="seek(current - 1)"
        >
          <v-icon size="18">mdi-step-backward</v-icon>
        </button>
        <button
          type="button"
          class="video-card__key"
          :title="t('work.room.video.forwardSecond')"
          :aria-label="t('work.room.video.forwardSecond')"
          data-testid="video-forward"
          @click="seek(current + 1)"
        >
          <v-icon size="18">mdi-step-forward</v-icon>
        </button>
      </template>
      <button
        type="button"
        class="video-card__key"
        :title="muted ? t('work.room.video.unmute') : t('work.room.video.mute')"
        :aria-label="muted ? t('work.room.video.unmute') : t('work.room.video.mute')"
        data-testid="video-mute"
        @click="toggleMute"
      >
        <v-icon size="18">{{ muted ? 'mdi-volume-off' : 'mdi-volume-high' }}</v-icon>
      </button>
      <div class="video-card__track">
        <input
          class="video-card__bar"
          type="range"
          min="0"
          :max="duration || 0"
          step="0.1"
          :value="current"
          :aria-label="t('work.room.video.seek')"
          :aria-valuetext="`${formatVideoTime(current)} / ${formatVideoTime(duration)}`"
          data-testid="video-bar"
          @input="onBar"
        />
      </div>
      <span class="video-card__time">{{ formatVideoTime(current) }} / {{ formatVideoTime(duration) }}</span>
      <button
        type="button"
        class="video-card__key video-card__rate"
        :title="t('work.room.video.rate', { rate })"
        :aria-label="t('work.room.video.rate', { rate })"
        data-testid="video-rate"
        @click="cycleRate"
      >
        {{ rate }}×
      </button>
    </div>

    <div class="video-card__actions">
      <BaseButton
        kind="ghost"
        prepend-icon="mdi-video-outline"
        append-icon="mdi-download-outline"
        class="video-card__download"
        :title="t('work.room.message.downloadFile', { name })"
        :aria-label="t('work.room.message.downloadFile', { name })"
        @click="emit('download')"
      >
        <span class="text-truncate">{{ meta }}</span>
      </BaseButton>
      <BaseButton
        v-if="canOpen"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-dock-right"
        class="video-card__open"
        :title="t('work.room.message.openFile', { name })"
        :aria-label="t('work.room.message.openFile', { name })"
        @click="emit('open')"
      >
        {{ t('work.room.file.open') }}
      </BaseButton>
      <!-- 灰着的按钮收不到悬停，为什么灰写在包着它的这一层上。 -->
      <span v-if="canLocate" :title="locateHint">
        <BaseButton
          kind="ghost"
          size="sm"
          prepend-icon="mdi-map-marker-plus-outline"
          class="video-card__locate"
          data-testid="video-locate"
          :disabled="!url || !!failure"
          :title="locateHint ? undefined : t('work.room.video.locateTime')"
          :aria-label="t('work.room.video.locateTime')"
          @click="locateCurrentTime"
        >
          {{ t('work.room.video.locateTime') }}
        </BaseButton>
      </span>
    </div>
  </div>
</template>

<style scoped>
/* 这张卡的形状照着原型：一圈线、一层浅影、圆角，画面在上面，控制条和身份行在下面。 */
.video-card {
  width: min(420px, 100%);
  min-width: 0;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
}
.video-card--panel {
  width: min(100%, 720px);
}
.video-card__stage {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: var(--video-ground);
}
/* 录屏自己的形状，最高 --video-max-height：竖屏手机录的收窄，不挤进一个宽盒子。 */
.video-card__video {
  --video-max-height: 480px;
  display: block;
  width: min(100%, calc(var(--video-max-height) * var(--video-ratio, 16 / 9)));
  max-height: var(--video-max-height);
  aspect-ratio: var(--video-ratio, 16 / 9);
  object-fit: contain;
}
.video-card__hit {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0;
  color: var(--video-ink);
  background: transparent;
  border: 0;
  cursor: pointer;
}
/* 还没打开的那一层：深色画面上只有一颗播放键和这一行字，和原型里的画面同一套。 */
.video-card__hit--closed {
  gap: 10px;
}
.video-card__open-label {
  color: var(--video-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  opacity: 0.88;
}
.video-card__play {
  /* 画面可能是浅色的（白底录屏）：白图标直接放上去就看不见了。垫一层半透明深底，
     和原型里那颗浮在画面上的播放键同一个意思。 */
  padding: 6px;
  background: var(--video-scrim);
  border-radius: 50%;
  box-shadow: var(--shadow-2);
}
.video-card__hit:hover .video-card__play,
.video-card__hit:focus-visible .video-card__play {
  opacity: 1;
}
.video-card__hit:focus-visible {
  outline: 2px solid var(--video-ink);
  outline-offset: -4px;
}
.video-card__busy,
.video-card__fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.video-card__busy {
  background: transparent;
}
.video-card__fallback {
  background: var(--fill);
}
.video-card__fail {
  margin: 0;
}
.video-card__ctl {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
}
.video-card__key {
  display: inline-grid;
  place-items: center;
  min-width: 28px;
  height: 28px;
  padding: 0 4px;
  color: var(--text);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.video-card__key:hover {
  background: var(--fill-2);
}
.video-card__key:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.video-card__track {
  position: relative;
  flex: 1;
  min-width: 0;
}
/* 原生 range 长得不像原型那条细杆，所以把轨道和滑块改写掉；键盘、读屏、aria-valuetext
   都还是浏览器给的。 */
.video-card__bar {
  display: block;
  width: 100%;
  height: 18px;
  margin: 0;
  appearance: none;
  background: transparent;
  cursor: pointer;
}
.video-card__bar::-webkit-slider-runnable-track {
  height: 4px;
  background: var(--fill-2);
  border-radius: 2px;
}
.video-card__bar::-moz-range-track {
  height: 4px;
  background: var(--fill-2);
  border-radius: 2px;
}
.video-card__bar::-webkit-slider-thumb {
  width: 12px;
  height: 12px;
  margin-top: -4px;
  appearance: none;
  background: var(--accent);
  border: 2px solid var(--surface);
  border-radius: 50%;
}
.video-card__bar::-moz-range-thumb {
  width: 12px;
  height: 12px;
  background: var(--accent);
  border: 2px solid var(--surface);
  border-radius: 50%;
}
.video-card__bar:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.video-card__time {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.video-card__rate {
  min-width: 34px;
}
.video-card__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 0 4px 4px;
}
.video-card__download {
  max-width: 100%;
  color: var(--faint);
  font-size: 12px;
}
/* 触屏上手指点得中：画出来 28px，能点的范围撑到 44×44（和看图浮层同一套做法）。 */
@media (pointer: coarse) {
  .video-card__key {
    position: relative;
  }
  .video-card__key::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(44px, 100%);
    transform: translate(-50%, -50%);
  }
}
</style>
