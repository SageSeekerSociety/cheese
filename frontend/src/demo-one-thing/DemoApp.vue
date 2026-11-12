<script setup lang="ts">
// The demo page: controls, the workspace scaled to the window, the caption
// under it and the three model cards. Two guided tours share the same
// mechanics: 「演示一：频道和一件事」 (STEPS, nine steps: a channel, a 一件事 from
// 讨论 to 采纳 with 芝士 working in its 工作台, 项目文件) and 「演示二：做事：
// 人和芝士」 (CODE_STEPS, ten steps, one model whoever does the work: a code
// file is read-only in 项目文件 and 「改这个文件」 opens a 一件事 I do, edited
// and run in its 工作台 and 采纳ed; a bigger change asked of 芝士 in a channel
// becomes a 讨论, then 开工, and 芝士 works in the 工作台 while I watch; I 接手,
// fix one line, 交给芝士 with a note; 王磊 采纳s his own change to the same
// file meanwhile, so at 采纳 the block both changed is picked by hand; then
// Data Lab: 领取 opens 「做 Data Lab」, written and run in its 工作台, 芝士 only
// hinting, and submitted from the 题目 card). Each step drives the same
// actions a click does; clicking by hand stops the script at any point, and
// switching tours starts the other one from a fresh state. In a project
// claimed from a 题目 the top bar also has 「模拟：离截止不到 24 小时」, which
// moves only the clock deadlines are read against. The 「方案」 menu switches
// the variants of the design questions still open (variants.ts: where the
// 工作台 opens); with none open it is not shown.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { CLOCK } from './course'
import { ASK, isClaim, ME } from './data'
import {
  acceptCopy,
  claim,
  confirmKick,
  editFile,
  finish,
  go,
  handToCheese,
  kickoff,
  liveStep,
  markDone,
  officialText,
  openBench,
  openFile,
  openSpace,
  openSubmit,
  openThing,
  peerChange,
  pickSide,
  reply,
  reset,
  runInBench,
  S,
  sayInThing,
  send,
  settle,
  settleUntil,
  submitTopic,
  submitVersions,
  suggAccept,
  takeOver,
  thingById,
  typeCopy,
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
      '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。领取时开好了一件事「做 …」，写代码、运行都在它的工作台里。标题下一行是它来自的课程和截止，圆点说交了没有；点开是卡片：工作台里运行到哪了、「接着做」、最近一次提交、「提交」。快截止或老师批改回来时它自己展开。',
      12,
      340
    )
    zone(
      '.ot-pane',
      '中间：频道或文件',
      '题面置顶在项目文件里。项目文件只放正式版，代码只读；在那件事的工作台里写、运行，芝士在它的对话里只给提示。',
      20,
      70
    )
    zone(
      '.ot-right [data-zone="right"]',
      '右栏：历史',
      '每次采纳都在历史里，能撤回。提交时默认交那件事里的当前版本，也可以挑正式版或历史里的任何一版。提交记录不在这里，在空间里的题目上。',
      20,
      110
    )
    notes.value = out
    return
  }
  zone('.ot-side', '左栏：去哪儿', '需要我的、频道（顺序固定）、项目文件、设置。', 12, 340)
  if (document.querySelector('#ot-app .ot-pane .ot-wb')) {
    zone(
      '.ot-pane',
      '中间：这件事的工作台',
      '这件事自己的那份文件、编辑器、下面是每次运行和输出。芝士做的时候只读，看它在改哪一行；人做的时候能改、能运行。正式版不动，采纳了才进。',
      20,
      70
    )
    zone(
      '.ot-right [data-zone="right"]',
      '右栏：还是这件事',
      '对话和改动；接手、交给芝士、改好了、采纳都在顶上。',
      20,
      110
    )
    notes.value = out
    return
  }
  zone(
    '.ot-pane',
    '中间：频道或文件',
    '频道有两种看法：对话，和频道文档（这个频道的事、芝士整理的定了 / 发现 / 没定、大家写的）。打开项目文件时是文件树和正式版。',
    20,
    70
  )
  zone(
    '.ot-right [data-zone="right"]',
    '右栏：选中的那一件',
    '只放一样东西。频道里是点开的那件事：讨论有对话、资料，开工后多出工作台、改动；什么都没点开就收起。打开文件时是它的历史，和正在改它的事（点开就是那件事的工作台）。',
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
    S.bench,
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

/** Type into a file of a 一件事's 工作台 the way a person types: take out what differs from `to`, then type the rest. */
async function typeCopyTo(id: string, path: string, to: string) {
  const from = thingById(id)?.copy?.[path]?.text ?? ''
  let a = 0
  while (a < from.length && a < to.length && from[a] === to[a]) a++
  let b = 0
  while (b < from.length - a && b < to.length - a && from[from.length - 1 - b] === to[to.length - 1 - b]) b++
  const head = to.slice(0, a)
  const tail = to.slice(to.length - b)
  const gone = from.slice(a, from.length - b)
  const add = to.slice(a, to.length - b)
  for (let i = gone.length - 1; i >= 0; i--) {
    typeCopy(id, path, head + gone.slice(0, i) + tail)
    await wait(90)
  }
  for (let i = 1; i <= add.length; i += 2) {
    typeCopy(id, path, head + add.slice(0, i) + tail)
    await wait(55)
  }
  typeCopy(id, path, to)
}

/** Wait until `ok` holds (芝士's timers move the 一件事 along on their own). */
async function until(ok: () => boolean) {
  while (!ok()) await wait(200)
}

/** A tab of the open 一件事, as clicking it does (W1 keeps the 工作台 in the center column). */
async function tabTo(key: 'chat' | 'changes', label: string) {
  await click(`.ot-thing .tabbar__tab[title="${label}"]`)
  S.rtab = key
  if (V.bench !== 'W1') S.bench = false
}

/** Open the 一件事's 工作台 the way the current 方案 offers it: W1's header button, else its tab. */
async function toBench(id: string) {
  await click(V.bench === 'W1' ? '#b-bench' : '.ot-thing .tabbar__tab[title="工作台"]')
  openBench(id)
}

/** A line of the open 工作台's editor, for the cursor. */
const benchLine = (n: number) => `.ot-wb .ot-carea__gutter span:nth-child(${n})`

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
    cap(4, '讨论原地变成任务：多出「工作台」和「改动」，进度走到「在做」。')
    await wait(2800)
  },
  async () => {
    await toBench('t4')
    cap(
      5,
      '「工作台」：这件事自己的那份文件。芝士在哪个文件、哪一行，计划走到哪一步，跑了什么命令、输出什么，都在这里，只读。'
    )
    for (let i = 1; i <= 4; i++) {
      await wait(1100)
      liveStep(i)
    }
    await wait(900)
  },
  async () => {
    finish()
    cap(6, '做完了，「等你采纳」：只有这个状态是橙色。结果表 <code>results/eval_results.md</code> 也在工作台里。')
    await wait(2000)
    await tabTo('changes', '改动')
    cap(6, '「改动」：工作台里那份和正式版的差别。采纳后才进正式版；要改就在对话里告诉芝士，或者接手。')
    await wait(2800)
  },
  async () => {
    await click('#b-accept')
    acceptCopy('t4')
    cap(7, '「采纳」：芝士那份进了正式版。成果直接就在项目文件里，不用再「保存」。')
    await wait(2800)
  },
  async () => {
    await click('[data-nav="files"] .v-list-item')
    go('files', { file: 'docs/组会纪要-11-07.md' })
    cap(
      8,
      '项目文件：文档、Word、代码、成果都在这一处，都是正式版。文档直接改，王磊也在改；标出来的那段是芝士的建议，卡片就在文档上面，还没生效。'
    )
    await wait(2800)
    await typeInto(TYPED_NOTE, (v) => (S.typed = v))
    await wait(800)
    await click('.doc-suggestion-card button:last-of-type')
    suggAccept()
    cap(8, '接受芝士的建议，它才进正式版。')
    await wait(1800)
    await moveTo('.ot-fp .ot-hist')
    cap(8, '右栏「历史」：每次生效记一条，包括刚才的采纳，写着是哪件事、谁做的，每条都能撤回。')
    await wait(3000)
  },
  async () => {
    await click('.ot-ftree__row[title="eval/preprocess.py"]')
    S.file = 'eval/preprocess.py'
    cap(
      9,
      '代码已经是采纳后的新版本（224）。代码在这里只读：要改就点「改这个文件」，开一件事，在它的工作台里改，做完一样采纳。'
    )
    await moveTo('#b-edit-file')
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
  if (k === 4) {
    openBench('t4')
    liveStep(4)
  }
  if (k === 5) {
    finish()
    S.rtab = 'changes'
    if (V.bench !== 'W1') S.bench = false
  }
  if (k === 6) acceptCopy('t4')
  if (k === 7) {
    go('files', { file: 'docs/组会纪要-11-07.md' })
    S.typed = TYPED_NOTE
    suggAccept()
  }
}

// ---- 演示二：做事：人和芝士 ----
const PRE = 'eval/preprocess.py'
const RET = 'eval/retrieval.py'
/** 林晓's own fix: the size the model takes, and the crop the paper uses. */
const fixed = (t: string) => t.replace('T.Resize(256),', 'T.Resize(224),\n        T.CenterCrop(224),')
/** Her one-line fix after 接手: 芝士's first try sorts the wrong way round. */
const sorted = (t: string) => t.replace('sims.argsort(dim=1)[', 'sims.argsort(dim=1, descending=True)[')
/** 王磊's own 一件事 meanwhile: a line on top (not 芝士's lines), and line 18, the one 芝士 rewrote, his own way. */
const wangleiEdit = (t: string) =>
  `# 评测入口：Flickr30K 1K / COCO 5K\n${t.replace('    return recall_at_1(sims)', '    return round(recall_at_1(sims), 1)')}`
const CODE_ASK = '<@cheese> 把 `eval/retrieval.py` 改成一次出 R@1 / R@5 / R@10'
const HAND_NOTE = '排序方向我改好了，你把 Flickr30K 1K 完整跑一遍。'
const HINT_ASK = '<@cheese> logicalNeg 从哪下手？'
/** Data Lab's first function, written the way the lab asks: only ~ and &. */
const BIT_XOR = '  return ~(~x & ~y) & ~(x & y);'
const withXor = (t: string) => t.replace(/(int bitXor\(int x, int y\) \{\n)\s*return 2;/, `$1${BIT_XOR}`)

/** The 一件事 opened by 「改这个文件」 on preprocess.py, the one asked of 芝士 about retrieval.py, Data Lab's. */
const myFix = () => S.things.find((t) => t.doer === ME && t.title === `改 ${PRE.split('/').pop()}`) ?? null
const asked = () => S.things.find((t) => t.id.startsWith('tc')) ?? null
const lab = () => thingById('w-datalab')
const toFiles = async (path: string) => {
  await click('[data-nav="files"] .v-list-item')
  openFile(path)
}
/** The line of a file's text that contains `bit`, 1-based. */
const lineOf = (text: string, bit: string) => text.split('\n').findIndex((l) => l.includes(bit)) + 1

const END = '只有一件事。谁做都在它的工作台里做：芝士做你看着，你做芝士帮着；做完采纳。'

const CODE_STEPS: (() => Promise<void>)[] = [
  async () => {
    reset()
    cap(1, '研究项目，项目文件里打开一个代码文件。这里是<b>正式版</b>，只读。')
    await click('[data-nav="files"] .v-list-item')
    go('files')
    await click(`.ot-ftree__row[title="${PRE}"]`)
    openFile(PRE)
    await wait(1600)
    await moveTo('[data-zone="official"]')
    cap(1, '项目文件里没有编辑器、没有「运行」、没有终端。')
    await wait(2200)
    await moveTo('#b-edit-file')
    cap(1, '只有一个按钮：「改这个文件」。右栏是这个文件的历史。')
    await wait(2600)
  },
  async () => {
    await click('#b-edit-file')
    editFile('research', PRE)
    cap(
      2,
      '开了一件事「改 preprocess.py」，林晓自己做，直接进它的<b>工作台</b>：这件事自己的那份文件。频道里也多了这件事。'
    )
    await wait(2800)
    const t = myFix()!
    await moveTo(benchLine(6), 160)
    await typeCopyTo(t.id, PRE, fixed(t.copy![PRE]!.text))
    cap(2, '<code>Resize(256)</code> 改成 224，加一行 <code>CenterCrop(224)</code>。改的是这件事的那份，正式版没动。')
    await wait(1600)
    await click('#b-run')
    runInBench(t.id, PRE)
    await until(() => !t.runs!.at(-1)!.running)
    cap(2, '「运行」也在这件事里：输出留在工作台下面，正好 224×224。')
    await wait(2600)
  },
  async () => {
    const t = myFix()!
    await click('#b-done')
    markDone(t.id)
    cap(3, '「改好了」：等采纳。王磊在这件事的对话里看了一眼。')
    await wait(2200)
    await tabTo('changes', '改动')
    cap(3, '「改动」：这件事和正式版的差别。')
    await wait(2200)
    await click('#b-accept')
    acceptCopy(t.id)
    cap(3, '「采纳」：自己做的小改动，也是这样进正式版。')
    await wait(1800)
    await toFiles(PRE)
    await moveTo('.ot-fp [data-via]')
    cap(3, '历史写着「林晓 采纳了「改 preprocess.py」（林晓做的）」，点事名回到那件事，也能撤回。')
    await wait(3200)
  },
  async () => {
    await click('[data-chan="c-exp"]')
    go('chan', { chan: 'c-exp', thing: null })
    S.chanView['c-exp'] = 'talk'
    cap(4, '大一点的改动交给芝士。在频道里 @芝士，提到那个文件。')
    await moveTo('.ot-composer__box')
    await typeInto(CODE_ASK, (v) => (S.typing = v), '<@cheese> ')
    await click('.ot-composer .composer-send')
    send()
    await wait(1800)
    const t = asked()!
    await click(`#card-${t.id}`)
    openThing(t.id)
    cap(4, '下面开出一个<b>讨论</b>：芝士先说这个文件现在怎样、打算怎么改，不动任何文件。')
    await wait(2800)
    await click('#b-kick')
    kickoff()
    await wait(1400)
    await click('#b-confirm')
    confirmKick()
    cap(4, '「开工」：交给芝士。')
    await wait(1600)
  },
  async () => {
    const t = asked()!
    await toBench(t.id)
    cap(5, '打开它的工作台看芝士做：它在改哪个文件、哪一行，一行行打出来；只读。')
    await until(() => !!t.runs?.length && !t.runs.at(-1)!.running)
    cap(5, '它跑了一遍，命令和输出都在下面：R@1 只有 0.0。')
    await until(() => t.last.includes('排序方向反了'))
    cap(5, '芝士在对话里说：排序方向反了，它要改一下。')
    await wait(1200)
  },
  async () => {
    const t = asked()!
    await click('#b-take')
    takeOver(t.id)
    cap(6, '林晓「接手」：芝士停下，在对话里说了一句。还是这个工作台，现在归林晓，可以改了。')
    await wait(2400)
    const text = t.copy![RET]!.text
    await moveTo(benchLine(lineOf(text, 'argsort')), 300)
    await typeCopyTo(t.id, RET, sorted(text))
    await click('#b-run')
    runInBench(t.id, RET)
    await until(() => !t.runs!.at(-1)!.running)
    cap(6, '改一行，加上 <code>descending=True</code>，再跑：R@1 79.0，对了。')
    await wait(2200)
    peerChange('wanglei', 'research', RET, 'retrieval.py 加一行说明', wangleiEdit(officialText('research', RET)))
    cap(6, '这时王磊也改了这个文件：他自己开了一件事，加一行说明，第 18 行改成保留一位小数，采纳进了正式版。')
    await wait(3000)
    await click('#b-hand')
    S.handNote = ''
    S.hand = true
    await moveTo('[data-hand-note]')
    await typeInto(HAND_NOTE, (v) => (S.handNote = v))
    await click('#b-hand-confirm')
    handToCheese(t.id, S.handNote)
    cap(6, '「交给芝士」，带一句话：芝士在同一个工作台里接着跑。')
    await until(() => t.status === 'wait')
    cap(6, '芝士跑完了：等采纳。')
    await wait(1800)
  },
  async () => {
    const t = asked()!
    await tabTo('changes', '改动')
    cap(7, '「改动」上面写着「正式版在这期间改过（王磊采纳的）」：采纳不会盖掉王磊的改动。')
    await wait(3000)
    await moveTo('[data-conflict]')
    cap(7, '王磊加的第 1 行和这件事改的不是一处，两边都留着；第 18 行两边都改了，并排摆出来，由采纳的人挑。')
    await wait(3000)
    await click('[data-conflict] [data-pick="theirs"]')
    pickSide(t.id, RET, 0, 'theirs')
    await wait(1000)
    await click('#b-accept')
    acceptCopy(t.id)
    cap(7, '「采纳」：先跟上最新的正式版，对话里一行说清楚留了谁的改动，再进正式版。')
    await wait(2200)
    await toFiles(RET)
    await moveTo('.ot-fp [data-via]')
    cap(7, '历史：「林晓 采纳了「…」（芝士做的，林晓接手改过）」，下面是王磊那条「（王磊做的）」。')
    await wait(3400)
  },
  async () => {
    await click('[data-space]')
    openSpace('tasks')
    cap(8, '换到课程：空间里领取 Data Lab。')
    await wait(1600)
    await click('#b-claim-datalab')
    claim('datalab')
    cap(8, '领取就开好了一件事「做 Data Lab」，林晓做。题目卡在左栏标题下面。')
    await wait(2400)
    await click('[data-origin-line]')
    S.cardOpen.datalab = true
    await wait(1200)
    await click('[data-origin-work]')
    openBench('w-datalab')
    cap(8, '「接着做」打开这件事的工作台：写 <code>bits.c</code>、运行，都在这里。')
    await wait(2200)
    const t = lab()!
    const text = t.copy!['bits.c']!.text
    await moveTo(benchLine(lineOf(text, 'int bitXor') + 1), 120)
    await typeCopyTo(t.id, 'bits.c', withXor(text))
    await click('#b-run')
    runInBench(t.id, 'bits.c')
    await until(() => !t.runs!.at(-1)!.running)
    cap(8, '运行 btest：bitXor 过了。题目卡上的进度读的就是这里的运行。题单的题目运行时还能挑样例或自己输入。')
    await wait(2600)
    await tabTo('chat', '对话')
    await moveTo('.ot-thing .ot-composer__box')
    await typeInto(HINT_ASK, (v) => (S.thingTyping = v), '<@cheese> ')
    await click('.ot-thing .composer-send')
    sayInThing()
    await wait(1800)
    cap(8, '在这件事的对话里问芝士：这门课它只给提示，不改代码。')
    await wait(2600)
  },
  async () => {
    S.cardOpen.datalab = true
    await click('#b-submit')
    openSubmit('datalab')
    cap(9, '「提交」在题目卡上。默认交「做 Data Lab」里的当前版本：不用先采纳，也可以挑正式版的版本。')
    await wait(1200)
    await moveTo('[data-version]')
    await wait(2600)
    await click('#b-submit-confirm')
    const v = submitVersions('datalab', 'bits.c')[0]!
    submitTopic('datalab', 'datalab', v, [])
    cap(9, '提交记在题目上，评测机马上评。')
    await wait(3000)
  },
  async () => {
    cursor.shown = false
    cap(10, END)
    await wait(6000)
  },
]

/** Bring the state to the start of step `k + 1` of 演示二 without animating. */
function instantCode(k: number) {
  if (k === 0) go('files', { file: PRE })
  if (k === 1) {
    editFile('research', PRE)
    const t = myFix()!
    typeCopy(t.id, PRE, fixed(t.copy![PRE]!.text))
    runInBench(t.id, PRE)
    settle()
  }
  if (k === 2) {
    const t = myFix()!
    markDone(t.id)
    settle()
    acceptCopy(t.id)
    openFile(PRE)
  }
  if (k === 3) {
    go('chan', { chan: 'c-exp', thing: null })
    S.typing = CODE_ASK
    send()
    settle()
    openThing(asked()!.id)
    confirmKick()
    S.morph = false
  }
  if (k === 4) {
    const t = asked()!
    openBench(t.id)
    settleUntil(() => t.last.includes('排序方向反了'))
  }
  if (k === 5) {
    const t = asked()!
    takeOver(t.id)
    settle()
    typeCopy(t.id, RET, sorted(t.copy![RET]!.text))
    runInBench(t.id, RET)
    settle()
    peerChange('wanglei', 'research', RET, 'retrieval.py 加一行说明', wangleiEdit(officialText('research', RET)))
    handToCheese(t.id, HAND_NOTE)
    settle()
  }
  if (k === 6) {
    const t = asked()!
    pickSide(t.id, RET, 0, 'theirs')
    acceptCopy(t.id)
    openFile(RET)
  }
  if (k === 7) {
    claim('datalab')
    S.cardOpen.datalab = true
    const t = lab()!
    openBench(t.id)
    typeCopy(t.id, 'bits.c', withXor(t.copy!['bits.c']!.text))
    runInBench(t.id, 'bits.c')
    settle()
  }
  if (k === 8) {
    submitTopic('datalab', 'datalab', submitVersions('datalab', 'bits.c')[0]!, [])
    settle()
  }
}

// ---- the tours ----
type TourKey = 'one' | 'code'
const TOURS: Record<TourKey, { label: string; steps: (() => Promise<void>)[]; instant: (k: number) => void }> = {
  one: { label: '演示一：频道和一件事', steps: STEPS, instant: instantOne },
  code: { label: '演示二：做事：人和芝士', steps: CODE_STEPS, instant: instantCode },
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
        <b>一件事：谁做都在它的工作台里</b>在频道里 @芝士
        开出一个讨论，芝士只出主意、查资料。商量好了点「开工」，芝士在这件事的工作台里做，你看着，随时能接手；自己做也是一件事。做完你采纳，才进正式版。
      </div>
      <div>
        <b>项目文件：一份正式版</b
        >文档、代码、Word、成果都在这里。文档直接改；代码只读，要改就点「改这个文件」，开一件事。每次采纳记进历史，写着是哪件事、谁做的，能撤回。
      </div>
      <div>
        <b>布局：中间频道，右边那件事</b
        >中间是频道，可以看对话，也可以看频道文档：这个频道的事、芝士整理的要点（每句都能跳回原消息）、大家写的。右栏只放选中的那一件：点开的讨论或任务；打开项目文件时，是文件的历史和正在改它的事。工作台放哪还在比，看「方案」。
      </div>
    </div>
  </div>
</template>
