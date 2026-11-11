<script setup lang="ts">
// The demo page: controls, the workspace scaled to the window, the caption
// under it and the three model cards. Two guided tours share the same
// mechanics: 「演示一：频道和一件事」 (STEPS, nine steps: a channel, a 一件事 from
// 讨论 to 采纳, 项目文件) and 「演示二：改代码」 (CODE_STEPS, eight steps: the
// code editor, a person editing 正式版 directly next to 王磊, 运行 in my own
// terminal, a branch merged back, then 芝士 asked to change a file: it works in
// its own copy inside a 一件事, the change waits as a 待定改动, and only a
// person's 采纳 puts it into 正式版). Each step drives the same actions a click
// does; clicking by hand stops the script at any point, and switching tours
// starts the other one from a fresh state. In a project claimed from a 题目
// the top bar also has 「模拟：离截止不到 24 小时」, which moves only the clock
// deadlines are read against. The 「方案」 menu switches the variants of the
// design questions still open (variants.ts); with none open it is not shown.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { CLOCK } from './course'
import { ASK, isClaim } from './data'
import {
  accept,
  acceptCopy,
  askAboutFile,
  askReview,
  codeText,
  confirmKick,
  copyThingFor,
  finish,
  finishCopy,
  go,
  kickoff,
  mergeBranch,
  openBranch,
  openFile,
  openThing,
  reply,
  reset,
  runCode,
  S,
  send,
  snapshot,
  suggAccept,
  typeCode,
} from './state'
import { QUESTIONS, setVariant, V } from './variants'
import Workspace from './Workspace.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
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
/** On a project claimed from a 题目: the only place the deadline clock changes anything on screen. */
const inClaim = computed(() => S.area === 'project' && isClaim(S.project))

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
const cap = (i: number, text: string) => Object.assign(caption, { n: `${i} / ${steps.value.length}`, text })

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
    zone(
      '.ot-side',
      '左栏：普通的项目',
      '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。标题下一行是它来自的课程和截止，圆点说交了没有；点开是卡片：本地运行到哪了、最近一次提交、「提交」。快截止或老师批改回来时它自己展开。',
      12,
      340
    )
    zone(
      '.ot-pane',
      '中间：频道或文件',
      '题面置顶在项目文件里，代码打开就能改、改了就生效，编辑器上只有「运行」。底下是你自己的终端，只有你看得到。',
      20,
      70
    )
    zone(
      '.ot-right [data-zone="right"]',
      '右栏：历史和对话',
      '每次改动都在历史里，能撤回；提交时可以挑历史里的任何一版。提交记录不在这里，在空间里的题目上。',
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
    Object.values(V).join(),
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

/** Move the cursor onto an element (its middle, or `dx` from its left edge). */
async function moveTo(sel: string, dx?: number) {
  await nextTick()
  const el = document.querySelector(`#ot-app ${sel}`)
  if (!el) return
  const r = rel(el)
  cursor.shown = true
  cursor.x = dx === undefined ? r.x + r.w / 2 : r.x + dx
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

/** Edit an open code file the way a person types: take out what differs from `to`, then type the rest. */
async function typeCodeTo(path: string, to: string) {
  const p = S.project
  const from = codeText(p, path)
  let a = 0
  while (a < from.length && a < to.length && from[a] === to[a]) a++
  let b = 0
  while (b < from.length - a && b < to.length - a && from[from.length - 1 - b] === to[to.length - 1 - b]) b++
  const head = to.slice(0, a)
  const tail = to.slice(to.length - b)
  const gone = from.slice(a, from.length - b)
  const add = to.slice(a, to.length - b)
  for (let i = gone.length - 1; i >= 0; i--) {
    typeCode(p, path, head + gone.slice(0, i) + tail)
    await wait(90)
  }
  for (let i = 1; i <= add.length; i += 2) {
    typeCode(p, path, head + add.slice(0, i) + tail)
    await wait(55)
  }
  typeCode(p, path, to)
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

/** Bring the state to the start of step `k + 1` of 演示一 without animating. */
function instantOne(k: number) {
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

// ---- 演示二：改代码 ----
const PRE = 'eval/preprocess.py'
const RET = 'eval/retrieval.py'
/** 林晓's own fix on 正式版: the size the model takes, and the crop the paper uses. */
const fixed = (t: string) => t.replace('T.Resize(256),', 'T.Resize(224),\n        T.CenterCrop(224),')
/** What she writes on her branch first: a line saying what the input is. */
const documented = (t: string) =>
  t.replace('def preprocess(img):', 'def preprocess(img):\n    """CLIP 的输入：224×224，和论文一样 center crop。"""')
const CODE_ASK = '改成一次出 R@1 / R@5 / R@10'
/** The line 林晓 selects before asking: `return recall_at_1(sims)`. */
const RET_LINE = 18
const selectLine = (path: string, line: number) =>
  (S.codeAt = { path, start: line, end: line, tick: (S.codeAt?.tick ?? 0) + 1 })

const VS =
  '<span class="ot-vs"><span><b>人</b>直接改正式版，改了大家马上看到<br>每次停顿记进历史，能撤回<br>想单独试，就开分支，看过再合回去</span>' +
  '<span><b>芝士</b>在一件事自己的那份上改，不碰正式版<br>改完是一份待定改动，人采纳了才进正式版<br>同样记进历史，能撤回</span></span>'

const CODE_STEPS: (() => Promise<void>)[] = [
  async () => {
    reset()
    cap(1, '项目文件里打开一个代码文件。它就是<b>正式版</b>，打开就能改，没有「改代码」这一步。')
    await click('[data-nav="files"] .v-list-item')
    go('files', { ftab: 'history' })
    await click(`.ot-ftree__row[title="${PRE}"]`)
    openFile(PRE, { ftab: 'history' })
    await wait(1800)
    await moveTo('.ot-ftree')
    cap(1, '左边是文件树：文档、代码、成果都在这一处。')
    await wait(1900)
    await moveTo('.ot-ced__faces')
    cap(1, '中间是编辑器。王磊也开着这个文件，他的光标带着名字。')
    await wait(2200)
    await moveTo('#b-run')
    cap(1, '「运行」在编辑器上方；底下是<b>输出</b>和<b>终端</b>，都是你自己的环境，只有你看得到。')
    await wait(2400)
    await moveTo('.ot-fp .tabbar')
    cap(1, '右栏是这个文件的「历史」「待定改动」「对话」：谁改过、芝士有什么还没生效的改动、频道里谁提到它。')
    await wait(3000)
  },
  async () => {
    cap(2, '林晓直接在正式版上改：<code>Resize(256)</code> 改成 224，再加一行 <code>CenterCrop(224)</code>。')
    await moveTo('.ot-carea__gutter span:nth-child(6)', 160)
    await typeCodeTo(PRE, fixed(codeText('research', PRE)))
    cap(2, '改了就生效，王磊那边马上看到。停顿一下，这次改动就记成一条「历史」。')
    await wait(2200)
    await moveTo('.ot-fp .ot-hist__row:first-child .ot-hist__undo')
    cap(2, '「林晓 改了 eval/preprocess.py」，旁边就是「撤回」：手滑了，一下退回去。')
    await wait(2800)
  },
  async () => {
    await click('#b-run')
    snapshot('research', PRE)
    runCode('research', PRE)
    cap(3, '「运行」：在你自己的环境里跑正式版现在这份代码。')
    await wait(1800)
    await moveTo('[data-zone="terminal"] .ot-ced__mine')
    cap(3, '输出和终端是你自己的，王磊看不到，芝士也不用它。跑出来正好 224×224。')
    await wait(3000)
  },
  async () => {
    await click('#b-branch')
    openBranch()
    cap(4, '想先单独试试：「开分支」。编辑器顶上写着分支名，在这里改不影响正式版，王磊看到的还是正式版。')
    await wait(2600)
    await moveTo('.ot-carea__gutter span:nth-child(5)', 220)
    await typeCodeTo(PRE, documented(codeText('research', PRE)))
    await wait(900)
    await click('#b-review')
    askReview()
    cap(4, '改好了「请人看一下」：王磊看过，分支栏上写着「王磊看过了」。')
    await wait(2600)
    await click('#b-merge-branch')
    mergeBranch()
    cap(4, '「合回正式版」：分支上的改动作为一条历史进了正式版，同样能撤回。')
    await wait(3000)
  },
  async () => {
    await click(`.ot-ftree__row[title="${RET}"]`)
    openFile(RET, { ftab: 'history' })
    cap(5, '换芝士来改。打开 <code>eval/retrieval.py</code>，选中只算 R@1 的那一行。')
    await moveTo(`.ot-carea__gutter span:nth-child(${RET_LINE})`, 120)
    selectLine(RET, RET_LINE)
    await wait(1400)
    await click('.ot-fp .tabbar__tab[title="对话"]')
    S.ftab = 'talk'
    cap(5, '在右栏「对话」里问芝士，选中的那一行跟着一起去。')
    await moveTo('.ot-fp .ot-composer__box')
    await typeInto(CODE_ASK, (v) => (S.asking = v))
    await click('.ot-fp .composer-send')
    askAboutFile()
    cap(5, '<b>芝士不直接改正式版。</b>它开了一件事，在这件事自己的那份上改；编辑器里还是正式版，一行没变。')
    await wait(1400)
    await moveTo('[data-cheese-copy]')
    await wait(2600)
  },
  async () => {
    // 芝士 takes a few seconds on its copy; the step goes on once the change waits for 采纳.
    while (copyThingFor('research', RET)?.status === 'run') await wait(200)
    await click('.ot-fp .tabbar__tab[title="待定改动"]')
    S.ftab = 'pending'
    cap(6, '芝士改完了。「待定改动」里是它那份和正式版的差别。')
    await wait(2400)
    await moveTo('#b-copy-more')
    cap(6, '可以「让芝士再改」（回到这件事的对话里告诉它）、「不要」，或者「采纳」。')
    await wait(3000)
  },
  async () => {
    await click('#b-copy-accept')
    const t = copyThingFor('research', RET)
    if (t) acceptCopy(t.id)
    S.ftab = 'history'
    cap(7, '「采纳」：芝士的改动这才进正式版，编辑器里就是新代码。')
    await wait(2200)
    await moveTo('.ot-fp .ot-hist__row:first-child .ot-hist__undo')
    cap(7, '历史里记一条「林晓 采纳了芝士的改动」，和人自己的改动一样，能撤回。')
    await wait(3000)
  },
  async () => {
    cursor.shown = false
    cap(8, VS)
    await wait(6000)
  },
]

/** Bring the state to the start of step `k + 1` of 演示二 without animating. */
function instantCode(k: number) {
  if (k === 0) openFile(PRE, { ftab: 'history' })
  if (k === 1) {
    typeCode('research', PRE, fixed(codeText('research', PRE)))
    snapshot('research', PRE)
  }
  if (k === 2) runCode('research', PRE)
  if (k === 3) {
    openBranch()
    typeCode('research', PRE, documented(codeText('research', PRE)))
    mergeBranch()
  }
  if (k === 4) {
    openFile(RET, { ftab: 'talk' })
    selectLine(RET, RET_LINE)
    S.codeSel = { path: RET, start: RET_LINE, end: RET_LINE }
    S.asking = CODE_ASK
    askAboutFile()
    const t = copyThingFor('research', RET)
    if (t) finishCopy(t.id)
  }
  if (k === 5) S.ftab = 'pending'
  if (k === 6) {
    const t = copyThingFor('research', RET)
    if (t) acceptCopy(t.id)
    S.ftab = 'history'
  }
}

// ---- the tours ----
type TourKey = 'one' | 'code'
const TOURS: Record<TourKey, { label: string; steps: (() => Promise<void>)[]; instant: (k: number) => void }> = {
  one: { label: '演示一：频道和一件事', steps: STEPS, instant: instantOne },
  code: { label: '演示二：改代码', steps: CODE_STEPS, instant: instantCode },
}
const TOUR_OPTIONS = (Object.keys(TOURS) as TourKey[]).map((k) => ({ value: k, label: TOURS[k].label }))
const tour = ref<TourKey>('one')
const steps = computed(() => TOURS[tour.value].steps)
const INTRO = '点「播放演示」走一遍；「显示说明」在界面上标出每一块是什么。任何时候都可以停下自己点。数据都是演示用的。'
/** Another tour starts from a fresh state, its first step not yet played. */
function pickTour(k: TourKey) {
  if (k === tour.value) return
  tour.value = k
  restart()
}

async function playFrom(i: number) {
  const my = ++run
  playing.value = true
  try {
    for (idx.value = i; idx.value < steps.value.length; idx.value++) {
      if (my !== run) return
      shown.value = idx.value
      await steps.value[idx.value]!()
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

/** Fresh state, then every step before `i` done at once. */
function fastForward(i: number) {
  reset()
  for (let j = 0; j < i; j++) TOURS[tour.value].instant(j)
  // Fast-forwarding replays the earlier steps' toasts; none of them belongs to this step.
  S.morph = false
  S.toast = ''
}

function jump(i: number) {
  stop()
  i = Math.max(0, Math.min(steps.value.length - 1, i))
  fastForward(i)
  void playFrom(i)
}

function playLabel() {
  const n = steps.value.length
  if (playing.value) return '暂停'
  if (idx.value < 0) return caption.n === `${n} / ${n}` ? '再放一遍' : '播放演示'
  return '继续播放'
}

function togglePlay() {
  if (playing.value) stop(false)
  else void playFrom(idx.value < 0 ? 0 : idx.value)
}

function restart() {
  stop()
  reset()
  CLOCK.dueSoon = false
  idx.value = -1
  shown.value = -1
  Object.assign(caption, { n: '演示', text: INTRO })
}

/** Play step `i` alone from its start state and stop at its end (the screenshot run uses it). */
async function only(i: number) {
  stop()
  fastForward(i)
  const my = ++run
  playing.value = true
  idx.value = i
  shown.value = i
  try {
    await steps.value[i]!()
  } catch {
    return
  }
  if (my !== run) return
  playing.value = false
  // 继续播放 picks up at the next step; after the last one the button offers to play it all again.
  idx.value = i + 1 < steps.value.length ? i + 1 : -1
  cursor.shown = false
  Object.assign(window, { __oneThingDemoDone: i })
}

// Lets a browser check pick a tour and play any one step on its own (the screenshot run uses it).
Object.assign(window, { __oneThingDemo: { jump, only, tour: (k: TourKey) => pickTour(k) } })

// A person's click inside the workspace takes over from the script.
function onUserClick(e: MouseEvent) {
  if (e.isTrusted && playing.value) stop(false)
}
</script>

<template>
  <div class="ot-page">
    <div class="ot-top">
      <h1 class="ot-top__title">okcheese「一件事」演示</h1>
      <SegmentedControl
        :model-value="tour"
        :options="TOUR_OPTIONS"
        label="选一段演示"
        size="md"
        data-tours
        @update:model-value="pickTour"
      />
      <BaseButton kind="primary" size="sm" :prepend-icon="playing ? 'mdi-pause' : 'mdi-play'" @click="togglePlay">{{
        playLabel()
      }}</BaseButton>
      <BaseButton kind="secondary" size="sm" :disabled="shown <= 0" @click="only(shown - 1)">上一步</BaseButton>
      <BaseButton kind="secondary" size="sm" :disabled="shown >= steps.length - 1" @click="only(shown + 1)"
        >下一步</BaseButton
      >
      <BaseButton kind="secondary" size="sm" :class="{ 'ot-top__on': notesOn }" @click="toggleNotes">{{
        notesOn ? '隐藏说明' : '显示说明'
      }}</BaseButton>
      <BaseButton kind="secondary" size="sm" @click="restart">重来</BaseButton>
      <BaseButton
        v-if="inClaim"
        kind="ghost"
        size="sm"
        :prepend-icon="CLOCK.dueSoon ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline'"
        :aria-pressed="CLOCK.dueSoon"
        title="只挪截止时间对照的那个钟：看提交入口在最后一天的样子"
        data-sim-due
        @click="CLOCK.dueSoon = !CLOCK.dueSoon"
        >模拟：离截止不到 24 小时</BaseButton
      >
      <v-menu
        v-if="QUESTIONS.length"
        v-model="variantsOpen"
        :close-on-content-click="false"
        location="bottom end"
        :offset="6"
      >
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="secondary"
            size="sm"
            append-icon="mdi-chevron-down"
            data-variants
            title="还没定的设计：每个问题挑一个方案看"
            >方案</BaseButton
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
