<script setup lang="ts">
// The demo page: controls, the workspace scaled to the window, the caption
// under it and the three model cards. The nine scripted steps drive the same
// actions a click does; clicking by hand stops the script at any point.
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { ASK } from './data'
import { accept, confirmKick, finish, go, kickoff, openThing, reply, reset, S, send, suggAccept } from './state'
import Workspace from './Workspace.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import vuetify from '@/plugins/vuetify'
import { applyResolvedTheme } from '@/theme'

const W = 1280
const H = 800

// ---- scale: the workspace keeps its desktop layout and shrinks to fit ----
const viewport = ref<HTMLElement | null>(null)
const scale = ref(1)
const offset = ref(0)
// The whole workspace stays on screen: it shrinks to the width and to the height left under the controls,
// so the composers at its bottom are never cut off.
function fit() {
  if (!viewport.value) return
  const room = window.innerHeight - viewport.value.getBoundingClientRect().top - 12
  scale.value = Math.min(1, viewport.value.clientWidth / W, Math.max(0.4, room / H))
  offset.value = Math.max(0, (viewport.value.clientWidth - W * scale.value) / 2)
}
let ro: ResizeObserver | null = null
onMounted(() => {
  fit()
  viewport.value?.addEventListener('click', onUserClick, true)
  window.addEventListener('resize', fit)
  ro = new ResizeObserver(fit)
  if (viewport.value) ro.observe(viewport.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  window.removeEventListener('resize', fit)
})

// ---- theme ----
const theme = ref<'light' | 'dark'>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyResolvedTheme(theme.value)
  vuetify.theme.change(theme.value)
}

// ---- toast clears itself ----
let toastTimer: number | undefined
watch(
  () => S.toastTick,
  () => {
    window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(() => (S.toast = ''), 2300)
  }
)

// ---- caption ----
const caption = reactive({
  n: '演示',
  text: '点「播放演示」走一遍；「显示说明」在界面上标出每一块是什么。任何时候都可以停下自己点。数据都是演示用的。',
})
const N = 9
const cap = (i: number, text: string) => Object.assign(caption, { n: `${i} / ${N}`, text })

// ---- geometry inside the workspace (unscaled px) ----
function rel(el: Element) {
  const app = document.getElementById('ot-app')!.getBoundingClientRect()
  const b = el.getBoundingClientRect()
  return {
    x: (b.left - app.left) / scale.value,
    y: (b.top - app.top) / scale.value,
    w: b.width / scale.value,
    h: b.height / scale.value,
  }
}

// ---- notes overlay ----
const notesOn = ref(false)
const notes = ref<
  { key: string; x: number; y: number; w: number; h: number; title: string; text: string; nx: number; ny: number }[]
>([])
function drawNotes() {
  if (!notesOn.value) {
    notes.value = []
    return
  }
  const out: typeof notes.value = []
  const zone = (sel: string, title: string, text: string, dx: number, dy: number) => {
    const el = document.querySelector(`#ot-app ${sel}`)
    if (!el) return
    const r = rel(el)
    out.push({ key: sel, x: r.x + 3, y: r.y + 3, w: r.w - 6, h: r.h - 6, title, text, nx: r.x + dx, ny: r.y + dy })
  }
  zone('.ot-side', '左栏：去哪儿', '需要我的、频道（顺序固定）、项目文件、设置。', 12, 340)
  zone('.ot-pane', '中间：频道或文件', '一直是频道，不会被一件事挤走；打开项目文件时是文件树和编辑器。', 20, 70)
  zone(
    '.ot-right [data-zone="right"]',
    '右栏：点开的那件事',
    '讨论：对话、资料；开工后：对话、现场、改动、成果。打开文件时：待定改动、历史。',
    20,
    110
  )
  notes.value = out
}
watch(
  () => [S.center, S.thing, S.chan, S.things.map((t) => t.status).join()],
  async () => {
    await nextTick()
    setTimeout(drawNotes, 380)
  }
)
function toggleNotes() {
  notesOn.value = !notesOn.value
  drawNotes()
}

// ---- the scripted cursor ----
const cursor = reactive({ shown: false, x: 0, y: 0 })
const ripples = ref<{ id: number; x: number; y: number }[]>([])
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
let run = 0
const idx = ref(-1)
/** The step on screen now (or last), for 上一步 / 下一步; -1 before the first. */
const shown = ref(-1)
const playing = ref(false)

function wait(ms: number) {
  const my = run
  return new Promise<void>((res, rej) =>
    setTimeout(() => (my === run ? res() : rej(new Error('stop'))), reduce ? Math.min(ms, 300) : ms)
  )
}

async function moveTo(sel: string) {
  await nextTick()
  const el = document.querySelector(`#ot-app ${sel}`)
  if (!el) return
  const r = rel(el)
  cursor.shown = true
  cursor.x = r.x + r.w / 2
  cursor.y = r.y + r.h / 2
  await wait(750)
}

async function click(sel: string) {
  await moveTo(sel)
  const id = Date.now()
  ripples.value.push({ id, x: cursor.x, y: cursor.y })
  setTimeout(() => (ripples.value = ripples.value.filter((r) => r.id !== id)), 600)
  await wait(250)
}

async function typeInto(full: string, set: (v: string) => void, lead = '') {
  set(lead)
  for (let i = lead.length + 1; i <= full.length; i += 2) {
    set(full.slice(0, i))
    await wait(45)
  }
  set(full)
}

const TYPED_NOTE = '林晓补充：COCO 5K 的结果见 eval_results.md。'

const STEPS: (() => Promise<void>)[] = [
  async () => {
    reset()
    cap(
      1,
      '中间是频道，右栏是这个频道的事。左边：需要我的、频道（顺序固定，「论文精读」有新消息只加粗）、项目文件、设置。'
    )
    await wait(4000)
  },
  async () => {
    cap(2, '在频道里 @芝士 说一句话。')
    await moveTo('.ot-composer__box')
    await typeInto(ASK, (v) => (S.typing = v), '<@cheese> ')
    await click('.ot-composer .composer-send')
    send()
    cap(2, '这句话下面开出一个<b>讨论</b>。芝士只出主意、查资料，不改东西。')
    await wait(2600)
  },
  async () => {
    await click('#card-t4')
    openThing('t4')
    cap(3, '点开：讨论在<b>右栏</b>打开，频道留在中间。队友插话，芝士跟着改计划。')
    await wait(1800)
    reply(2, '陈思远：COCO 要用 5K 测试集')
    await wait(1400)
    reply(3, '芝士：好，先对齐预处理，再统一脚本、跑两个测试集')
    await wait(1600)
    await click('.ot-thing .tabbar__tab[title="资料"]')
    S.rtab = 'sources'
    cap(3, '「资料」：芝士读过的项目文件，写着谁、什么时候。')
    await wait(2400)
  },
  async () => {
    await click('#b-kick')
    kickoff()
    cap(4, '点「开工」：交给芝士，只定负责人和交付什么。')
    await wait(2600)
    await click('#b-confirm')
    confirmKick()
    cap(4, '讨论原地变成任务：右栏变宽，多出现场、改动、成果，进度走到「在做」。')
    await wait(2800)
  },
  async () => {
    cap(5, '「现场」：计划一步步打勾，下面是芝士每一步做了什么、跑出了什么。')
    for (let i = 1; i <= 4; i++) {
      await wait(900)
      S.live = i
    }
    await wait(600)
  },
  async () => {
    finish()
    cap(6, '做完了，「等你采纳」：只有这个状态是橙色。')
    await wait(1600)
    await click('.ot-thing .tabbar__tab[title="改动"]')
    S.rtab = 'changes'
    cap(6, '「改动」：这件事在自己那份上改的东西，采纳后才合进正式版。')
    await wait(2800)
  },
  async () => {
    await click('#b-accept')
    accept()
    cap(7, '「采纳 · 合进正式版」。成果直接就在项目文件里，不用再「保存」。')
    await wait(2800)
  },
  async () => {
    await click('[data-nav="files"] .v-list-item')
    go('files', { file: 'docs/组会纪要-11-07.md', ftab: 'pending' })
    cap(8, '项目文件：文档、Word、代码、成果都在这一处。文档直接改，王磊也在改；标出来的那段是芝士的建议，还没生效。')
    await wait(2800)
    await typeInto(TYPED_NOTE, (v) => (S.typed = v))
    await wait(800)
    await click('.doc-suggestion-card button:last-of-type')
    suggAccept()
    cap(8, '接受芝士的建议，它才进正式版。')
    await wait(1800)
    await click('.ot-fp .tabbar__tab[title="历史"]')
    S.ftab = 'history'
    cap(8, '「历史」：每次生效记一条，包括刚才的采纳，每条都能撤回。')
    await wait(3000)
  },
  async () => {
    await click('.file-item[title^="eval/preprocess.py"]')
    S.file = 'eval/preprocess.py'
    cap(9, '代码在正式版上只读，已经是采纳后的新版本（224）。要改就开一件事，在任务那份里改，做完再合。')
    await wait(3600)
    cursor.shown = false
  },
]

/** Bring the state to the start of step `k` without animating. */
function instant(k: number) {
  if (k === 1) {
    S.typing = ASK
    send()
  }
  if (k === 2) {
    openThing('t4')
    reply(3, '芝士：好，先对齐预处理，再统一脚本、跑两个测试集')
    S.rtab = 'sources'
  }
  if (k === 3) confirmKick()
  if (k === 4) S.live = 4
  if (k === 5) {
    finish()
    S.rtab = 'changes'
  }
  if (k === 6) accept()
  if (k === 7) {
    go('files', { file: 'docs/组会纪要-11-07.md', ftab: 'history' })
    S.typed = TYPED_NOTE
    suggAccept()
  }
}

async function playFrom(i: number) {
  const my = ++run
  playing.value = true
  try {
    for (idx.value = i; idx.value < STEPS.length; idx.value++) {
      if (my !== run) return
      shown.value = idx.value
      await STEPS[idx.value]!()
    }
  } catch {
    return
  }
  playing.value = false
  idx.value = -1
}

function stop(hide = true) {
  run++
  playing.value = false
  if (hide) cursor.shown = false
}

function jump(i: number) {
  stop()
  i = Math.max(0, Math.min(STEPS.length - 1, i))
  reset()
  for (let j = 0; j < i; j++) instant(j)
  // Fast-forwarding replays the earlier steps' toasts; none of them belongs to this step.
  S.morph = false
  S.toast = ''
  void playFrom(i)
}

function playLabel() {
  if (playing.value) return '暂停'
  if (idx.value < 0) return caption.n === `${N} / ${N}` ? '再放一遍' : '播放演示'
  return '继续播放'
}

function togglePlay() {
  if (playing.value) stop(false)
  else void playFrom(idx.value < 0 ? 0 : idx.value)
}

function restart() {
  stop()
  reset()
  idx.value = -1
  shown.value = -1
  Object.assign(caption, { n: '演示', text: '点「播放演示」走一遍；「显示说明」在界面上标出每一块是什么。' })
}

/** Play step `i` alone from its start state and stop at its end (the screenshot run uses it). */
async function only(i: number) {
  stop()
  reset()
  for (let j = 0; j < i; j++) instant(j)
  // Fast-forwarding replays the earlier steps' toasts; none of them belongs to this step.
  S.morph = false
  S.toast = ''
  const my = ++run
  playing.value = true
  idx.value = i
  shown.value = i
  try {
    await STEPS[i]!()
  } catch {
    return
  }
  if (my !== run) return
  playing.value = false
  // 继续播放 picks up at the next step; after the last one the button offers to play it all again.
  idx.value = i + 1 < STEPS.length ? i + 1 : -1
  cursor.shown = false
  Object.assign(window, { __oneThingDemoDone: i })
}

// Lets a browser check play any one step on its own (the screenshot run in the PR uses it).
Object.assign(window, { __oneThingDemo: { jump, only } })

// A person's click inside the workspace takes over from the script.
function onUserClick(e: MouseEvent) {
  if (e.isTrusted && playing.value) stop(false)
}
</script>

<template>
  <div class="ot-page">
    <div class="ot-top">
      <h1 class="ot-top__title">okcheese「一件事」演示</h1>
      <BaseButton kind="primary" size="sm" :prepend-icon="playing ? 'mdi-pause' : 'mdi-play'" @click="togglePlay">{{
        playLabel()
      }}</BaseButton>
      <BaseButton kind="secondary" size="sm" :disabled="shown <= 0" @click="only(shown - 1)">上一步</BaseButton>
      <BaseButton kind="secondary" size="sm" :disabled="shown >= N - 1" @click="only(shown + 1)">下一步</BaseButton>
      <BaseButton kind="secondary" size="sm" :class="{ 'ot-top__on': notesOn }" @click="toggleNotes">{{
        notesOn ? '隐藏说明' : '显示说明'
      }}</BaseButton>
      <BaseButton kind="secondary" size="sm" @click="restart">重来</BaseButton>
      <BaseButton
        kind="ghost"
        size="sm"
        :icon="theme === 'dark' ? 'mdi-weather-sunny' : 'mdi-weather-night'"
        :aria-label="theme === 'dark' ? '换成浅色' : '换成深色'"
        :title="theme === 'dark' ? '换成浅色' : '换成深色'"
        @click="toggleTheme"
      />
    </div>

    <div ref="viewport" class="ot-viewport" :style="{ height: `${H * scale}px` }">
      <div class="ot-scaler" :style="{ transform: `scale(${scale})`, left: `${offset}px` }">
        <Workspace :notes="notes">
          <div
            v-show="cursor.shown"
            class="ot-cursor"
            :style="{ transform: `translate(${cursor.x - 4}px, ${cursor.y - 3}px)` }"
            aria-hidden="true"
          >
            <svg width="22" height="22" viewBox="0 0 22 22">
              <path
                d="M3 2 L3 18 L7.5 13.8 L10.6 20.5 L13.4 19.2 L10.4 12.6 L16.5 12.6 Z"
                class="ot-cursor__shape"
                stroke-width="1.4"
                stroke-linejoin="round"
              />
            </svg>
          </div>
          <div
            v-for="r in ripples"
            :key="r.id"
            class="ot-ripple"
            :style="{ left: `${r.x - 15}px`, top: `${r.y - 15}px` }"
            aria-hidden="true"
          />
        </Workspace>
      </div>
    </div>

    <div class="ot-caption" aria-live="polite">
      <span class="ot-caption__n">{{ caption.n }}</span>
      <!-- eslint-disable-next-line vue/no-v-html -- caption text is the demo's own fixed strings -->
      <span class="ot-caption__t" v-html="caption.text" />
    </div>

    <div class="ot-model">
      <div>
        <b>一件事：讨论 → 开工 → 采纳</b>在频道里 @芝士
        开出一个讨论，芝士只出主意、查资料。商量好了点「开工」，交给芝士做，人也能动手；做完你采纳，合进正式版。
      </div>
      <div>
        <b>项目文件：一份正式版</b
        >文档、代码、Word、成果都在这里。每人改自己那份：文档实时合，代码做完验证再合。人的改动直接生效，芝士的改动先待定，一键接受。
      </div>
      <div>
        <b>布局：中间频道，右边那件事</b
        >中间一直是频道，点开的讨论或任务在右栏。打开项目文件时，中间是文件，右栏是它的待定改动和历史。
      </div>
    </div>
  </div>
</template>
