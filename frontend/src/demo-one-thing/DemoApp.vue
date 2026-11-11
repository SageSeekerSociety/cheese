<script setup lang="ts">
// The demo page: controls, the workspace scaled to the window, the caption
// under it and the three model cards. The nine scripted steps drive the same
// actions a click does; clicking by hand stops the script at any point. The
// controls' 「方案」 menu switches the variants of the design questions still
// open (variants.ts); a switch redraws in place and keeps where the demo is.
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { ASK } from './data'
import { accept, confirmKick, finish, go, kickoff, openThing, reply, reset, S, send, suggAccept } from './state'
import { QUESTIONS, setVariant, V } from './variants'
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

const variantsOpen = ref(false)

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
/** What a claimed project's sidebar note says about the way back to its 题目, per 方案. */
const ORIGIN_NOTE: Record<string, string> = {
  A: '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。标题下一行写着它来自哪道题，点它回到空间里的这道题，在那里提交、看提交记录。',
  B: '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。它来自哪道题写在中间的顶栏上，点它回到空间里的这道题，在那里提交。',
  C: '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。它来自的题目在右栏「题目」里：要求、截止、提交记录、「提交」。',
  D: '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。顶上的卡片是它来自的题目：截止、交到哪了、「提交」；点题目名回到空间里的这道题。',
}
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
  if (S.area === 'home' && S.center === 'space') {
    zone(
      '.ot-side',
      '左栏：空间',
      '公告、资料库、题目（全部，和老师分的类）。空间不是项目，也没有频道；它在最左边一栏有自己的图标。「管理」只有老师和助教看得到。',
      12,
      340
    )
    zone(
      '.ot-pane',
      '中间：题目',
      '每道题：分类、评分方式（评测机评或老师评）、截止、个人还是组队、我或我们组做到哪了。点开一道题看它的提交记录。「领取」建一个项目：个人题目在你自己的团队下，组队题目在一起领取的团队下。',
      20,
      70
    )
    notes.value = out
    return
  }
  if (S.area === 'home') {
    zone(
      '.ot-side',
      '左栏：首页',
      '待办、我的团队（各自的项目）、我加入的空间。课程是一个空间：从这里进，也可以点最左边一栏首页下面它的图标。',
      12,
      340
    )
    zone(
      '.ot-pane',
      '中间：待办',
      '所有项目和空间里等我的事：快到的截止、等我采纳的任务。点一条就到那个项目或那道题。',
      20,
      70
    )
    notes.value = out
    return
  }
  if (S.project !== 'research') {
    zone('.ot-side', '左栏：普通的项目', ORIGIN_NOTE[V.submit], 12, 340)
    zone(
      '.ot-pane',
      '中间：频道或文件',
      '题面置顶在项目文件里，代码打开就能改、改了就生效，编辑器上只有「运行」。底下是你自己的终端，只有你看得到。',
      20,
      70
    )
    zone(
      '.ot-right [data-zone="right"]',
      V.submit === 'C' ? '右栏：历史、对话和题目' : '右栏：历史和对话',
      V.submit === 'C'
        ? '每次改动都在历史里，能撤回；提交时可以挑历史里的任何一版。「题目」是它来自的题目：要求、提交记录和「提交」，和空间里那一页是同一个。'
        : '每次改动都在历史里，能撤回；提交时可以挑历史里的任何一版。提交记录不在这里，在空间里的题目上。',
      20,
      110
    )
    notes.value = out
    return
  }
  zone('.ot-side', '左栏：去哪儿', '需要我的、频道（顺序固定）、项目文件、设置。', 12, 340)
  zone(
    '.ot-pane',
    '中间：频道或文件',
    '频道有两种看法：对话，和频道文档（这个频道的事、芝士整理的定了 / 发现 / 没定、大家写的）。一直是频道，不会被一件事挤走；打开项目文件时是文件树和编辑器。',
    20,
    70
  )
  zone(
    '.ot-right [data-zone="right"]',
    '右栏：选中的那一件',
    '只放一样东西。频道里是点开的那件事：讨论有对话、资料，开工后多出现场、改动、成果；什么都没点开就收起。打开文件时是它的历史、待定改动，和频道里提到它的对话（可以带着它问芝士）。',
    20,
    110
  )
  notes.value = out
}
watch(
  () => [
    S.area,
    S.project,
    S.center,
    S.spacePage,
    S.spaceTopic,
    S.submitAsk,
    S.thing,
    S.chan,
    S.chanView[S.chan],
    S.things.map((t) => t.status).join(),
    S.ftab,
    V.submit,
    V.space,
  ],
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
      '中间是频道：「对话」，或频道的「文档」（这个频道的事、芝士整理的要点、大家写的；积了几条没读的，进去先看文档）。右栏只放点开的那一件事，平时收起。左边：需要我的、频道（顺序固定）、项目文件、设置。'
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
    cap(6, '「改动」：芝士在自己那份上改的东西，只读，只有芝士改它。采纳后才进正式版；要改就在对话里告诉芝士。')
    await wait(2800)
  },
  async () => {
    await click('#b-accept')
    accept()
    cap(7, '「采纳」：芝士那份进了正式版。成果直接就在项目文件里，不用再「保存」。')
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
    await click('.ot-ftree__row[title="eval/preprocess.py"]')
    S.file = 'eval/preprocess.py'
    cap(
      9,
      '代码已经是采纳后的新版本（224）。人直接改正式版，和文档一样：打开就能改，王磊也在这个文件里，每次改动自动记进历史、能撤回。想隔离就「开分支」。芝士的改动才要人采纳。'
    )
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
      <v-menu v-model="variantsOpen" :close-on-content-click="false" location="bottom end" :offset="6">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="secondary"
            size="sm"
            append-icon="mdi-chevron-down"
            data-variants
            title="还没定的设计：每个问题挑一个方案看"
            >方案 {{ V.submit }} · {{ V.space }}</BaseButton
          >
        </template>
        <div class="ot-variants" role="dialog" aria-label="方案">
          <section v-for="q in QUESTIONS" :key="q.key" class="ot-variants__q" :data-question="q.key">
            <h3 class="ot-variants__title">{{ q.title }}</h3>
            <button
              v-for="o in q.options"
              :key="o.key"
              type="button"
              class="ot-variants__opt"
              :class="{ 'is-on': V[q.key] === o.key }"
              :aria-pressed="V[q.key] === o.key"
              :data-variant="`${q.key}:${o.key}`"
              @click="setVariant(q.key, o.key)"
            >
              <v-icon size="16" :icon="V[q.key] === o.key ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'" />
              <span class="ot-variants__main">
                <b>{{ o.label }}</b>
                <span>{{ o.note }}</span>
              </span>
            </button>
          </section>
          <p class="ot-variants__foot">切换不会重来，演示停在哪就还在哪。地址栏的链接带着当前方案。</p>
        </div>
      </v-menu>
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
        开出一个讨论，芝士只出主意、查资料。商量好了点「开工」，交给芝士在它自己那份上做；做完你采纳，才进正式版。
      </div>
      <div>
        <b>项目文件：一份正式版</b
        >文档、代码、Word、成果都在这里。人直接改正式版，代码和文档一样，大家同时在改；每次改动自动记进历史，能撤回，想隔离就开分支。芝士的改动先待定，一键接受。
      </div>
      <div>
        <b>布局：中间频道，右边那件事</b
        >中间一直是频道，可以看对话，也可以看频道文档：这个频道的事、芝士整理的要点（每句都能跳回原消息）、大家写的。右栏只放选中的那一件：点开的讨论或任务；打开项目文件时，是文件的待定改动和历史。
      </div>
    </div>
  </div>
</template>
