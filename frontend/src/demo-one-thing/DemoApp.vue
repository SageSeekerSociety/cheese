<script setup lang="ts">
// The demo page: controls, the workspace scaled to the window, the caption
// under it and the three model cards. Two guided tours share the same
// mechanics: 「演示一：频道和一件事」 (STEPS, nine steps: a channel, a 一件事 from
// 讨论 to 采纳, opened as a page of its own on 文件 while 芝士 works, then
// 项目文件, where documents and code are both typed into directly) and
// 「演示二：做事：人和芝士」 (CODE_STEPS, seven beats: 林晓 types into
// preprocess.py in 项目文件 and runs it, a run record on the file; @芝士 in
// # 实验记录 about retrieval.py becomes a 讨论 and then a 一件事, whose card
// says what 芝士 is doing; opened, it lands on 文件; 王磊 edits retrieval.py
// in 项目文件 meanwhile, next to the lines 芝士's marker shows, and 芝士 redoes
// its change on his version at its next run and says so; 林晓 接手, fixes one
// line, 交给芝士; in 改动 a second edit of 王磊's makes the freshness badge go
// stale until 芝士 has caught up, then 采纳; then Data Lab: 领取, bits.c typed
// and run in 项目文件, @芝士 giving only a hint, 提交 from the 题目 card). Each
// step drives the same actions a click does; clicking by hand stops the
// script at any point, and switching tours starts the other one from a fresh
// state. In a project claimed from a 题目 the top bar also has 「模拟：离截止不到
// 24 小时」, which moves only the clock deadlines are read against.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { CLOCK } from './course'
import { ASK, CLAIMED, isClaim, MINUTES_DOC, type ProjectKey } from './data'
import {
  acceptCopy,
  claim,
  confirmKick,
  editDoc,
  editProject,
  finish,
  freshness,
  go,
  handToCheese,
  kickoff,
  liveStep,
  openFile,
  openSpace,
  openSubmit,
  openThing,
  peerEdit,
  projectText,
  reply,
  reset,
  runInProject,
  runInThing,
  S,
  send,
  settle,
  settleUntil,
  submitTopic,
  suggAccept,
  takeOver,
  thingById,
  typeCopy,
  versionsOf,
} from './state'
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
      '领取来的题目就是一个项目，属于一个团队：频道、项目文件、设置，和别的项目一样。标题下一行是它来自的课程和截止，圆点说交了没有；点开是卡片：最近一次运行通过了多少、「接着做」打开要写的文件、最近一次提交、「提交」。快截止或老师批改回来时它自己展开。',
      12,
      340
    )
    zone(
      '.ot-pane',
      '中间：频道或文件',
      '题面置顶在项目文件里。代码直接写，「运行」跑题目带来的命令，输出记在文件上。这个项目设成「芝士不能改文件」：@芝士 开出的讨论只给提示，没有「开工」。',
      20,
      70
    )
    zone(
      '.ot-right [data-zone="right"]',
      '右栏：历史和运行',
      '每段改动都在历史里，能撤回；「运行」是这个文件的每次运行。提交时挑项目文件里这个文件的一个版本。提交记录不在这里，在空间里的题目上。',
      20,
      110
    )
    notes.value = out
    return
  }
  zone('.ot-side', '左栏：去哪儿', '需要我的、频道（顺序固定）、项目文件、设置。', 12, 340)
  const open = S.thing ? thingById(S.thing) : null
  if (open && open.status !== 'talk' && S.center === 'chan') {
    zone(
      '.ot-right [data-zone="right"]',
      '这件事，整页',
      '开工后的一件事像文档一样整页打开：对话、文件、改动。「文件」是它在项目最新版上面改的那几处，别的文件跟着项目走；芝士做的时候只读，接手了才能改。「改动」是和正式版的差别，基于最新版时才能采纳。左上角回到频道。',
      20,
      110
    )
    notes.value = out
    return
  }
  zone(
    '.ot-pane',
    '中间：频道或文件',
    '频道有两种看法：对话，和频道文档（这个频道的事、芝士整理的定了 / 发现 / 没定、大家写的）。打开项目文件时是文件树和文件本身：文档、代码都直接改，代码多一个「运行」。',
    20,
    70
  )
  zone(
    '.ot-right [data-zone="right"]',
    '右栏：选中的那一件',
    '只放一样东西。频道里是点开的讨论，什么都没点开就收起；开工后的一件事整页打开。打开文件时是它的历史、运行记录，和正在改它的事。',
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
    S.fileTab,
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

/** Typing that turns `from` into `to` the way a person types: take out what differs, then type the rest. */
async function typeFromTo(from: string, to: string, set: (v: string) => void) {
  let a = 0
  while (a < from.length && a < to.length && from[a] === to[a]) a++
  let b = 0
  while (b < from.length - a && b < to.length - a && from[from.length - 1 - b] === to[to.length - 1 - b]) b++
  const head = to.slice(0, a)
  const tail = to.slice(to.length - b)
  const gone = from.slice(a, from.length - b)
  const add = to.slice(a, to.length - b)
  for (let i = gone.length - 1; i >= 0; i--) {
    set(head + gone.slice(0, i) + tail)
    await wait(90)
  }
  for (let i = 1; i <= add.length; i += 2) {
    set(head + add.slice(0, i) + tail)
    await wait(55)
  }
  set(to)
}

/** A person typing into a project file in 项目文件 (林晓 by default, or a teammate). */
const typeProject = (p: ProjectKey, path: string, to: string, who?: string) =>
  typeFromTo(projectText(p, path), to, (v) => (who ? peerEdit(who, p, path, v) : editProject(p, path, v)))

/** 林晓 typing into a 一件事's file after 接手. */
const typeCopyTo = (id: string, path: string, to: string) =>
  typeFromTo(thingById(id)?.copy?.[path]?.text ?? '', to, (v) => typeCopy(id, path, v))

/** Wait until `ok` holds (芝士's timers move the 一件事 along on their own). */
async function until(ok: () => boolean) {
  while (!ok()) await wait(200)
}

/** A face of the open 一件事, as clicking its tab does. */
async function tabTo(key: 'chat' | 'files' | 'changes', label: string) {
  await click(`.ot-thing .tabbar__tab[title="${label}"]`)
  S.rtab = key
}

/** A line of the code editor on screen (项目文件's, or a 一件事's 文件), for the cursor. */
const codeLine = (n: number, scope = '') => `${scope} .ot-carea__gutter span:nth-child(${n})`

const TYPED_NOTE = '林晓补充：COCO 5K 的结果见 eval_results.md。'

const STEPS: (() => Promise<void>)[] = [
  async () => {
    reset()
    cap(
      1,
      '中间是频道：「对话」，或频道的「文档」（这个频道的事、芝士整理的要点、大家写的；积了几条没读的，进去先看文档）。右栏只放点开的那一件，平时收起。左边：需要我的、频道（顺序固定）、项目文件、设置。'
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
    cap(4, '讨论变成一件事，右栏收起。频道里它的卡片实时写着芝士在做什么：改哪个文件、第几行、跑了几次。')
    await moveTo('#card-t4')
    await wait(2800)
  },
  async () => {
    await click('#card-t4')
    openThing('t4')
    cap(
      5,
      '点卡片：开工后的一件事像文档一样<b>整页</b>打开，落在「文件」：它改的文件、芝士在哪一行、计划走到哪、每次运行和输出，只读。左上角回到频道。'
    )
    for (let i = 1; i <= 4; i++) {
      await wait(1100)
      liveStep(i)
    }
    await wait(900)
  },
  async () => {
    finish()
    cap(6, '做完了，「等你采纳」：只有这个状态是橙色。结果表 <code>results/eval_results.md</code> 也在「文件」里。')
    await wait(2000)
    await tabTo('changes', '改动')
    cap(6, '「改动」：和正式版的差别，写着「基于最新版」。要改就在对话里告诉芝士，或者接手。')
    await wait(2800)
  },
  async () => {
    await click('#b-accept')
    acceptCopy('t4')
    cap(7, '「采纳」：芝士的改动进了项目。成果直接就在项目文件里，不用再「保存」。')
    await wait(2800)
  },
  async () => {
    await click('[data-nav="files"] .v-list-item')
    go('files', { file: MINUTES_DOC })
    cap(
      8,
      '项目文件就是项目本身：文档、Word、代码、成果都在这里。文档直接改，王磊也在改；标出来的那段是芝士的建议，卡片就在文档上面，还没生效。'
    )
    await wait(2800)
    await typeInto(TYPED_NOTE, (v) => {
      S.typed = v
      editDoc('research', MINUTES_DOC)
    })
    await wait(800)
    await click('.doc-suggestion-card button:last-of-type')
    suggAccept()
    cap(8, '接受芝士的建议，它才改进文档。')
    await wait(1800)
    await moveTo('.ot-fp .ot-hist')
    cap(8, '右栏「历史」：每段改动记一条，「林晓 改了…」，刚才的采纳也在，写着是哪件事、谁做的，每条都能撤回。')
    await wait(3000)
  },
  async () => {
    await click('.ot-ftree__row[title="eval/preprocess.py"]')
    openFile('eval/preprocess.py')
    cap(
      9,
      '代码也一样：已经是采纳后的新版本（224），直接改就行。多一个「运行」：在项目的环境里跑，输出是这个文件上的一条运行记录。'
    )
    await moveTo('#b-run')
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
    openThing('t4')
    liveStep(4)
  }
  if (k === 5) {
    finish()
    S.rtab = 'changes'
  }
  if (k === 6) acceptCopy('t4')
  if (k === 7) {
    go('files', { file: MINUTES_DOC })
    S.typed = TYPED_NOTE
    editDoc('research', MINUTES_DOC)
    suggAccept()
  }
}

// ---- 演示二：做事：人和芝士 ----
const PRE = 'eval/preprocess.py'
const RET = 'eval/retrieval.py'
/** 林晓's own fix in 项目文件: the size the model takes, and the crop the paper uses. */
const fixed = (t: string) => t.replace('T.Resize(256),', 'T.Resize(224),\n        T.CenterCrop(224),')
/** Her one-line fix after 接手: 芝士's first try sorts the wrong way round. */
const sorted = (t: string) => t.replace('sims.argsort(dim=1)[', 'sims.argsort(dim=1, descending=True)[')
/** 王磊's edit in 项目文件 while 芝士 works, typed in two places: the import, then cosine similarity on the line
 *  right above the one 芝士 rewrites. */
const cosineImport = (t: string) => t.replace('import torch\n', 'import torch\nimport torch.nn.functional as F\n')
const cosineSims = (t: string) =>
  t.replace(
    '    sims = image_feats @ text_feats.T',
    '    sims = F.normalize(image_feats, dim=-1) @ F.normalize(text_feats, dim=-1).T'
  )
/** His second edit, while the 一件事 waits for 采纳: a line on top, away from 芝士's. */
const headline = (t: string) => `# 评测入口：Flickr30K 1K / COCO 5K\n${t}`
const CODE_ASK = '<@cheese> 把 `eval/retrieval.py` 改成一次出 R@1 / R@5 / R@10'
const HAND_NOTE = '排序方向我改好了，你把 Flickr30K 1K 完整跑一遍。'
const HINT_ASK = '<@cheese> logicalNeg 从哪下手？'
/** Data Lab's first function, written the way the lab asks: only ~ and &. */
const BIT_XOR = '  return ~(~x & ~y) & ~(x & y);'
const withXor = (t: string) => t.replace(/(int bitXor\(int x, int y\) \{\n)\s*return 2;/, `$1${BIT_XOR}`)

/** The 一件事 asked of 芝士 about retrieval.py, and the hint 讨论 in Data Lab. */
const asked = () => S.things.find((t) => t.id.startsWith('tc')) ?? null
const hint = () => S.things.find((t) => t.id.startsWith('th')) ?? null
const toFiles = async (path: string) => {
  await click('[data-nav="files"] .v-list-item')
  openFile(path)
  S.fileTab = 'history'
}
/** The line of a text that contains `bit`, 1-based. */
const lineOf = (text: string, bit: string) => text.split('\n').findIndex((l) => l.includes(bit)) + 1
/** 芝士 has said, in the 一件事, that it redid its change on someone's edit. */
const rebased = () => !!asked()?.talk?.some((l) => 'text' in l && l.text.includes('重做了我的改动'))

const END = '人直接改项目；芝士在它的一件事里做，跟着项目的最新版走，做完你采纳。'

const CODE_STEPS: (() => Promise<void>)[] = [
  async () => {
    reset()
    cap(1, '项目文件就是项目本身。打开 <code>eval/preprocess.py</code>：王磊也在这个文件里，他的光标看得到。')
    await click('[data-nav="files"] .v-list-item')
    go('files')
    await click(`.ot-ftree__row[title="${PRE}"]`)
    openFile(PRE)
    await wait(2000)
    await moveTo(codeLine(6, '.ot-ced'), 200)
    await typeProject('research', PRE, fixed(projectText('research', PRE)))
    cap(
      1,
      '林晓直接改，和改文档一样：<code>Resize(256)</code> 换成 224，加一行 <code>CenterCrop(224)</code>。自动保存。'
    )
    await wait(1400)
    await moveTo('.ot-fp .ot-hist__row')
    cap(1, '右栏「历史」多了一条「林晓 改了 eval/preprocess.py」，能撤回。')
    await wait(2400)
    await click('#b-run')
    runInProject('research', PRE)
    await until(() => !S.fileRuns[`research:${PRE}`]?.at(-1)?.running)
    cap(
      1,
      '「运行」：项目的环境跑这个文件，输出是这个文件上的一条<b>运行记录</b>：谁、什么时候、哪个版本。最近一次收在文件下面。'
    )
    await wait(2600)
    await click('[data-file-tab] .segmented__option:last-child')
    S.fileTab = 'runs'
    cap(1, '右栏「运行」：这个文件的每次运行。运行不进历史；想让大家看到，点「发到频道」。')
    await wait(2800)
  },
  async () => {
    await click('[data-chan="c-exp"]')
    go('chan', { chan: 'c-exp', thing: null })
    S.chanView['c-exp'] = 'talk'
    cap(2, '大一点的改动交给芝士：在 # 实验记录 里 @芝士，提到那个文件。')
    await moveTo('.ot-composer__box')
    await typeInto(CODE_ASK, (v) => (S.typing = v), '<@cheese> ')
    await click('.ot-composer .composer-send')
    send()
    await wait(1600)
    const t = asked()!
    await click(`#card-${t.id}`)
    openThing(t.id)
    cap(2, '下面开出一个<b>讨论</b>，在右栏：芝士先说这个文件现在怎样、打算怎么改，不动任何文件。')
    await until(() => t.replies >= 2)
    await wait(2600)
    await click('#b-kick')
    kickoff()
    await wait(1400)
    await click('#b-confirm')
    confirmKick()
    cap(2, '「开工」：讨论变成一件事，右栏收起。频道里的卡片实时写着它在做什么：芝士在改哪一行、跑了几次。')
    await moveTo(`#card-${t.id}`)
    await wait(3400)
  },
  async () => {
    const t = asked()!
    await click(`#card-${t.id}`)
    openThing(t.id)
    cap(3, '点卡片：这件事<b>整页</b>打开，落在「文件」：芝士改的文件、它在打哪一行，下面是每次运行。左上角回到频道。')
    await until(() => !!t.runs?.length && !t.runs.at(-1)!.running)
    cap(3, '它跑了一遍，命令和输出都在下面：R@1 只有 0.0。它在对话里说要再查查。')
    await until(() => t.last.includes('在查'))
    await wait(1600)
  },
  async () => {
    const t = asked()!
    await click('#b-thing-back')
    go('chan', { chan: 'c-exp', thing: null })
    await toFiles(RET)
    cap(
      4,
      '这时王磊在项目文件里直接改 <code>retrieval.py</code>。芝士那件事在改的几行带着标记，不是锁，只是让人看到会撞在哪。'
    )
    await moveTo('[data-cheese-mark]')
    await wait(2200)
    await moveTo(codeLine(2, '.ot-ced'), 120)
    await typeProject('research', RET, cosineImport(projectText('research', RET)), 'wanglei')
    await moveTo(codeLine(18, '.ot-ced'), 260)
    await typeProject('research', RET, cosineSims(projectText('research', RET)), 'wanglei')
    cap(4, '王磊把相似度换成余弦，正好是芝士改的那几行的上一行。')
    await wait(2200)
    await click('[data-things-on-file]')
    openThing(t.id, 'files', RET)
    cap(4, '回到这件事：它记下了「王磊改了项目里的 retrieval.py」。芝士下次运行前，先跟上项目的最新版。')
    await until(rebased)
    await tabTo('chat', '对话')
    cap(4, '芝士在王磊的版本上重做了自己的改动，在对话里一句话说清楚，重跑了。上面一行写着「基于最新版」和跑的时间。')
    await wait(3400)
  },
  async () => {
    const t = asked()!
    await tabTo('files', '文件')
    await click('#b-take')
    takeOver(t.id)
    cap(5, '林晓看出来了：argsort 默认从小到大，排序方向反了。她「接手」：芝士停下，这件事的文件归她改。')
    await wait(2200)
    const text = t.copy![RET]!.text
    await moveTo(codeLine(lineOf(text, 'argsort'), '.ot-wb'), 300)
    await typeCopyTo(t.id, RET, sorted(text))
    await click('#b-run-thing')
    runInThing(t.id, RET)
    await until(() => !t.runs!.at(-1)!.running)
    cap(5, '改一行，加上 <code>descending=True</code>，在这件事里跑：R@1 79.4，对了。')
    await wait(2200)
    await click('#b-hand')
    S.handNote = ''
    S.hand = true
    await moveTo('[data-hand-note]')
    await typeInto(HAND_NOTE, (v) => (S.handNote = v))
    await click('#b-hand-confirm')
    handToCheese(t.id, S.handNote)
    cap(5, '「交给芝士」，带一句话：芝士先跟上项目的最新版，再跑完整的 Flickr30K 1K。')
    await until(() => t.status === 'wait')
    cap(5, '跑完了，等你采纳。')
    await wait(1800)
  },
  async () => {
    const t = asked()!
    await tabTo('changes', '改动')
    cap(6, '「改动」：和正式版的差别，只有芝士和林晓改的这几行。徽标写着「基于最新版」和刚才跑的时间。')
    await moveTo('[data-fresh]')
    await wait(2400)
    await moveTo('#b-accept-tab')
    await wait(600)
    peerEdit('wanglei', 'research', RET, headline(projectText('research', RET)))
    cap(6, '王磊又在项目里加了一行说明：徽标变成「项目又改过 · 芝士在跟上」，采纳先停着。')
    await wait(1600)
    await until(() => freshness(t).fresh && !t.catching)
    cap(6, '他改的不是同一处，芝士直接合进来，又跑了一遍：回到「基于最新版」，可以采纳了。')
    await wait(2400)
    await click('#b-accept')
    acceptCopy(t.id)
    cap(6, '「采纳」：干干净净进了项目。')
    await wait(1800)
    await toFiles(RET)
    await moveTo('.ot-fp [data-via]')
    cap(
      6,
      '历史：「林晓 采纳了「…」（芝士做的，林晓接手改过）」，下面是王磊的两条「王磊 改了 eval/retrieval.py」，都能撤回。'
    )
    await wait(3600)
  },
  async () => {
    await click('[data-space]')
    openSpace('tasks')
    cap(7, '换到课程：空间里领取 Data Lab。领取只建一个项目，在你自己的团队下。')
    await wait(1600)
    await click('#b-claim-datalab')
    claim('datalab')
    await wait(1600)
    await click('[data-origin-line]')
    S.cardOpen.datalab = true
    await wait(1000)
    await click('[data-origin-work]')
    openFile('bits.c')
    cap(7, '题目卡上「接着做」打开 <code>bits.c</code>，在项目文件里直接写。')
    await wait(1600)
    const text = projectText('datalab', 'bits.c')
    await moveTo(codeLine(lineOf(text, 'int bitXor') + 1, '.ot-ced'), 120)
    await typeProject('datalab', 'bits.c', withXor(text))
    await click('#b-run')
    runInProject('datalab', 'bits.c')
    await until(() => !S.fileRuns['datalab:bits.c']?.at(-1)?.running)
    cap(7, '「运行」跑题目带来的 <code>make btest</code>：bitXor 过了。输出记在 bits.c 上，题目卡的进度读的就是它。')
    await wait(2800)
    await click(`[data-chan="${CLAIMED.datalab.chan}"]`)
    go('chan', { chan: CLAIMED.datalab.chan, thing: null })
    await moveTo('.ot-composer__box')
    await typeInto(HINT_ASK, (v) => (S.typing = v), '<@cheese> ')
    await click('.ot-composer .composer-send')
    send()
    await until(() => (hint()?.replies ?? 0) >= 2)
    await click(`#card-${hint()!.id}`)
    openThing(hint()!.id)
    cap(7, '在频道里 @芝士：这个项目设成「芝士不能改文件」，开出的讨论只给提示，没有「开工」。')
    await wait(3600)
    await click('#b-submit')
    openSubmit('datalab')
    cap(7, '「提交」在题目卡上：冻结项目文件里 bits.c 的一个版本（默认现在这一版），交到题目上。')
    await moveTo('[data-version]')
    await wait(2600)
    await click('#b-submit-confirm')
    submitTopic('datalab', 'datalab', versionsOf('datalab', 'bits.c')[0]!, [])
    cap(7, '提交记在题目上，评测机马上评。')
    await wait(2600)
    cursor.shown = false
    cap(7, END)
    await wait(5000)
  },
]

/** Bring the state to the start of step `k + 1` of 演示二 without animating. */
function instantCode(k: number) {
  if (k === 0) {
    openFile(PRE)
    editProject('research', PRE, fixed(projectText('research', PRE)))
    runInProject('research', PRE)
    settle()
    S.fileTab = 'runs'
  }
  if (k === 1) {
    go('chan', { chan: 'c-exp', thing: null })
    S.typing = CODE_ASK
    send()
    settleUntil(() => (asked()?.replies ?? 0) >= 2)
    openThing(asked()!.id)
    confirmKick()
    S.morph = ''
  }
  if (k === 2) {
    const t = asked()!
    openThing(t.id)
    settleUntil(() => t.last.includes('在查'))
  }
  if (k === 3) {
    const t = asked()!
    go('chan', { chan: 'c-exp', thing: null })
    openFile(RET)
    peerEdit('wanglei', 'research', RET, cosineSims(cosineImport(projectText('research', RET))))
    openThing(t.id, 'chat')
    settleUntil(rebased)
    settleUntil(() => !t.runs!.at(-1)!.running)
  }
  if (k === 4) {
    const t = asked()!
    S.rtab = 'files'
    takeOver(t.id)
    typeCopy(t.id, RET, sorted(t.copy![RET]!.text))
    runInThing(t.id, RET)
    settle()
    handToCheese(t.id, HAND_NOTE)
    settle()
  }
  if (k === 5) {
    const t = asked()!
    peerEdit('wanglei', 'research', RET, headline(projectText('research', RET)))
    settle()
    acceptCopy(t.id)
    openFile(RET)
    S.fileTab = 'history'
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
  S.morph = ''
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
        <b>一件事：交给芝士的事</b>在频道里 @芝士
        开出一个讨论，芝士只出主意、查资料。商量好了点「开工」，芝士在这件事里改，改在项目最新版的上面；别人照常改项目，它跟着跟上。随时能接手。做完你采纳，才进项目。
      </div>
      <div>
        <b>项目文件：项目本身</b
        >文档、代码、Word、成果都在这里，人直接改，光标看得到，每段保存记进历史，能撤回。代码多一个「运行」：输出是这个文件上的运行记录，不进历史。
      </div>
      <div>
        <b>布局：中间频道，右边那件事</b
        >中间是频道，可以看对话，也可以看频道文档。右栏只放选中的那一件：点开的讨论；开工后的一件事像文档一样整页打开，返回回到频道。打开项目文件时，右栏是文件的历史、运行记录和正在改它的事。
      </div>
    </div>
  </div>
</template>
