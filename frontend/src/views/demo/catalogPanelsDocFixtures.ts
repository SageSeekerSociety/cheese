/**
 * 「文档面板」那一册的样例数据：头像、大纲、版本对比、评论串、改动记录，和一把真
 * 的编辑器。
 *
 * 单独一份，和 `catalogPanelsFixtures.ts` 同一个理由：那一份已经够长，而这些只被
 * `catalogPanelsDoc.ts` 用。这里的东西尽量重名册和真组件要的那几种类型，不另造一
 * 套「像文档」的假结构 —— 面板拿到的是 `DocThreadState`、`OutlineHeading`、
 * `VersionDiff` 这些真类型，预览站里画出来的也就是真的那种样子。
 */
import type { Editor } from '@tiptap/core'
import type { DocPeer } from '@/composables/useDocCollab'
import type { PresetContext } from '@/lib/docAgent'
import type { DocVersion, DocVersionPage } from '@/lib/docHistory'
import type { OutlineHeading } from '@/lib/docOutline'
import type { SlashItem } from '@/lib/docSlashMenu'
import type { DocComment, DocThread, DocThreadActions, DocThreadState, ThreadPlace } from '@/lib/docThreadTypes'
import type { VersionDiff } from '@/lib/versionDiff'

import { Editor as TiptapEditor } from '@tiptap/core'

import { AGENT_NAME, docSession } from './catalogFixtures'
import { PANEL_NAMES } from './catalogPanelsFixtures'

import { DocEditor } from '@/components/panels/doc/docEditor'
import { docExtensions } from '@/lib/docSchema'
import { SLASH_ITEMS } from '@/lib/docSlashMenu'
import { versionDiff } from '@/lib/versionDiff'

/** 这一册里当「AI 队友」用的名字和 handle（正文里点名读成它）。 */
export const DOC_AGENT = AGENT_NAME
export const DOC_AGENT_HANDLE = 'cheese'
export const DOC_PERSON_HANDLE = 'wang'
export const DOC_PERSON = PANEL_NAMES[DOC_PERSON_HANDLE] ?? '王长鑫'
export const DOC_MENTION_NAMES = PANEL_NAMES

/** 打开这一册时正文里写的那一篇。 */
export const DOC_MARKDOWN = [
  '# 上线过程',
  '',
  '九月起搜索框改为常驻，下面是两周后的复盘。',
  '',
  '## 改了什么',
  '',
  '顶栏那颗按钮去掉了，搜索框直接摆在标题下面，回车就搜。',
].join('\n')

/**
 * 一把真的编辑器：内容为空也行，浮条、slash 菜单、链接框要的是它身上的编辑器，
 * 不是文档内容。用完由条目自己留着（预览站不回收组件里的东西）。
 */
export function demoEditor(markdown = ''): Editor {
  const editor = new TiptapEditor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
  if (markdown) editor.commands.setContent(markdown, { contentType: 'markdown' })
  return editor
}

/**
 * 一把**带撤销栈**的编辑器。共享文档里的撤销来自协同扩展，独立的一把（`standalone`）
 * 换成 StarterKit 自带的那个 —— 手机上那条浮条（`variant: 'bar'`）要的正是它，别的
 * 那几格不用。
 */
export function demoEditorStandalone(markdown = ''): Editor {
  const editor = new TiptapEditor({
    element: document.createElement('div'),
    extensions: docExtensions({ standalone: true }),
    content: '',
  })
  if (markdown) editor.commands.setContent(markdown, { contentType: 'markdown' })
  return editor
}

/**
 * 一把正文列认得出来的编辑器 —— `panel-doc-prose` 那一格要它。
 *
 * `doc-prose` 这个类名不是编辑器自己起的：真的那一把由 `DocSurface` 建编辑器时写在
 * `editorProps` 上，正文列的排版有一半按它挂（`.doc-editor :deep(.doc-prose ...)`）。
 * 预览站里没有 DocSurface 那一层，只能建的时候自己加上 —— 不加，那一格画出来就是一段
 * 没排过版的裸正文，而排版正是这一格要看的东西。
 *
 * 建的是 `DocEditor`（`@tiptap/vue-3` 那一支，`DocSurface` 建的也正是它），不是上面两把
 * 用的 `@tiptap/core` 版：正文列是这一册里唯一用 `EditorContent` 挂编辑器的一件，而
 * `EditorContent` 拿到没被 `markRaw` 过的编辑器，就会和自己的效果互相触发、绕不出来，
 * 整条测试进程停在那儿。真的那两把都建 vue 版，这一把也得是。
 */
export function demoProseEditor(markdown = ''): Editor {
  const editor = new DocEditor({
    extensions: docExtensions(),
    editable: true,
    editorProps: { attributes: { class: 'doc-prose' } },
  })
  if (markdown) editor.commands.setContent(markdown, { contentType: 'markdown' })
  return editor
}

/** 这篇标签页里的那一份协同文档？不用 —— 只给 DocSurface 一格就够了。 */
export function docSurfaceSession() {
  return docSession(DOC_MARKDOWN)
}

/** 顶栏上「谁也在这儿」：六个人，只画四个，多的收成「+2」。 */
export const DOC_PEERS: DocPeer[] = [
  { clientId: 1, handle: 'cheese', agent: true, name: '芝士', avatar: '', color: '#7c5cff' },
  { clientId: 2, handle: 'wang', agent: false, name: '王长鑫', avatar: '', color: '#3b82f6' },
  { clientId: 3, handle: 'lixue', agent: false, name: '李雪', avatar: '', color: '#10b981' },
  { clientId: 4, handle: 'zhaolei', agent: false, name: '赵磊', avatar: '', color: '#f59e0b' },
  { clientId: 5, handle: 'sunqi', agent: false, name: '孙琪', avatar: '', color: '#ef4444' },
  { clientId: 6, handle: 'chenmo', agent: false, name: '陈默', avatar: '', color: '#06b6d4' },
]

/** 一篇还没起标题的文档：大纲那一格要的空态。 */
export const DOC_HEADINGS_EMPTY: OutlineHeading[] = []

/** 正文里的三级标题。 */
export const DOC_HEADINGS: OutlineHeading[] = [
  { level: 1, text: '上线过程', pos: 1 },
  { level: 2, text: '改了什么', pos: 42 },
  { level: 3, text: '搜索的入口', pos: 88 },
]

/** 上一版和这一版之间改了哪几段。 */
export const DOC_DIFF: VersionDiff = versionDiff(
  '# 上线过程\n\n九月起搜索框改为常驻。\n\n## 复盘\n\n搜索次数翻倍。',
  '# 上线过程\n\n九月起搜索框改为常驻。\n\n## 复盘\n\n搜索次数翻了三倍，用时少了一秒。'
)

/** 读了两版一模一样的正文：没有改动那一句。 */
export const DOC_DIFF_EMPTY: VersionDiff = { rows: [], added: 0, removed: 0, changed: 0 }

/** 一条评论（开一串的那条，或者一串里的回复）。 */
function docComment(id: string, author: string, content: string, anchorQuote: string | null, at: string): DocComment {
  return { id, author, content, anchor_quote: anchorQuote, created_at: at }
}

const T0 = '2026-09-30T02:00:00.000Z'

/** 一串没解决的评：人开的头，「芝士」在答。 */
const OPEN_THREAD: DocThread = {
  comment: docComment('th-open', DOC_PERSON_HANDLE, '这里的用时要不要一起写进去？', '搜索次数翻倍。', T0),
  revision: 3,
  state: 'open',
  replies: [
    {
      sequence: 1,
      comment: docComment(
        'reply-1',
        DOC_AGENT_HANDLE,
        '写进去了，改成「用时少了一秒」。',
        null,
        '2026-09-30T02:04:00.000Z'
      ),
    },
  ],
  answering: 'working',
}

/** 一串已经解决的评。 */
const RESOLVED_THREAD: DocThread = {
  comment: docComment('th-done', DOC_PERSON_HANDLE, '标题改成「上线过程」吧。', '上线记录', '2026-09-29T08:00:00.000Z'),
  revision: 2,
  state: 'resolved',
  replies: [],
}

/** 一条都没有的评。 */
export function docThreadStateEmpty(): DocThreadState {
  return { threads: [], activity: {}, errors: {}, busy: false, unknown: null }
}

/** 两串评：一串没解决（芝士正在答），一串已解决。 */
export function docThreadState(): DocThreadState {
  return {
    threads: [OPEN_THREAD, RESOLVED_THREAD],
    activity: { 'th-open': { state: 'working', tool: 'cheese_doc.edit' } },
    errors: {},
    busy: false,
    unknown: null,
  }
}

/** 写评论、解决、重开……预览站什么都不写：都是不动的空动作。 */
export const DOC_THREAD_ACTIONS: DocThreadActions = {
  reply: async () => {},
  resolve: async () => {},
  reopen: async () => {},
  recover: async () => undefined,
  stopAgent: async () => {},
}

/** 评论那几串的锚点都在正文里（那一格只用得着「还在」这一种）。 */
export function docPlaceOf(): ThreadPlace {
  return 'marked'
}

/** 名字读法：handle → 名册上的名字。 */
export function docNameOf(handle: string): string {
  return PANEL_NAMES[handle] ?? handle
}

/** 一页修改记录：越新的越靠前。 */
export const DOC_VERSIONS: DocVersion[] = [
  {
    version: 4,
    content: '# 上线过程\n\n九月起搜索框改为常驻。\n\n## 复盘\n\n搜索次数翻了三倍。',
    actor: DOC_PERSON_HANDLE,
    created_at: T0,
  },
  {
    version: 3,
    content: '# 上线过程\n\n九月起搜索框改为常驻。\n\n## 复盘\n\n搜索次数翻倍。',
    actor: DOC_AGENT_HANDLE,
    created_at: '2026-09-29T10:00:00.000Z',
  },
]

/** 没有改动记录：空态那一句。 */
export function docVersionPageEmpty(): Promise<DocVersionPage> {
  return Promise.resolve({ versions: [], cursor: null })
}

/** 读一页修改记录。 */
export function docVersionPage(): Promise<DocVersionPage> {
  return Promise.resolve({ versions: DOC_VERSIONS, cursor: null })
}

/** 一把 slash 菜单的表（块类型那一张，真的那一份）。 */
export const DOC_SLASH_ITEMS: SlashItem[] = SLASH_ITEMS

/** 「选中一段字问 AI 队友」的上下文：能改、有列表、是中文。 */
export const DOC_PRESET_EDIT: PresetContext = { editable: true, list: false, chinese: true }
/** 「对整篇问」的上下文。 */
export const DOC_PRESET_DOC: PresetContext = { editable: true, list: true, chinese: true }
