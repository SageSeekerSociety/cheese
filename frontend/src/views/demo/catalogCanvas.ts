/**
 * 画布在预览站里的条目：文档里的一块画布（项目地图），画布本身，和画布上的一个格子。
 *
 * 读写都在内存里（`catalogCanvasFixtures.ts`）：在「文档里的画布」那几格拖格子、连线、
 * 双击改字都会生效，刷新就回到原样。
 */
import type { CanvasHost } from '@/types/canvas'
import type { CatalogEntry } from './catalog'

import { CANVAS_DOC, emptyBoard, memoryCanvas, projectMap } from './catalogCanvasFixtures'
import { docPanelProps, docSession } from './catalogFixtures'

import CanvasBoard from '@/components/canvas/CanvasBoard.vue'
import CanvasCardNode from '@/components/canvas/CanvasCardNode.vue'
import CanvasBlockView from '@/components/panels/doc/blocks/CanvasBlockView.vue'
import PanelDocView from '@/components/panels/PanelDocView.vue'

const MAP = projectMap()
const [GOAL, INDEX, UI_CARD] = MAP.cards

function host(): CanvasHost {
  return { ...memoryCanvas(), openTask: () => {} }
}

/** 节点视图收到的那一组 props（tiptap 的 `nodeViewProps`）。 */
function blockViewProps(
  over: { editable?: boolean; canvas?: string; title?: string; about?: string } = {}
): Record<string, unknown> {
  const {
    editable = true,
    canvas = MAP.id,
    title = '项目地图',
    about = '新版搜索要做的几件事和它们之间的关系。',
  } = over
  return {
    editor: { isEditable: editable },
    node: { type: { name: 'canvas' }, attrs: { canvas, title, about, pending: '' } },
    decorations: [],
    selected: false,
    extension: { name: 'canvas', options: { host: host() } },
    getPos: () => 0,
    updateAttributes: () => {},
    deleteNode: () => {},
    view: {},
    innerDecorations: {},
    HTMLAttributes: {},
  }
}

export const CANVAS_ENTRIES: CatalogEntry[] = [
  {
    id: 'doc-canvas',
    title: 'PanelDocView · 画布',
    about:
      '文档里的一块画布：原文里只有一行 :::canvas 编号 标题 和一句说明，画布另存。宽度跟着正文那一栏，拖底边改高度，右上角全屏。',
    file: 'src/components/panels/doc/blocks/CanvasBlockView.vue',
    component: PanelDocView,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '在文档里',
        note: '拖格子挪位置，拖格子边上的圆点连线，双击格子改字，双击空白处加一个格子，双击连线写线上的字；关联了任务的格子顶上是任务的状态圆点，点一下去任务。',
        props: docPanelProps({ session: docSession(CANVAS_DOC), canvas: memoryCanvas() }),
        expect: '十月底上线新版搜索',
      },
      {
        name: '只读',
        note: '能看、能缩放、能全屏，不能挪、不能改。',
        props: docPanelProps({
          session: docSession(CANVAS_DOC),
          canvas: memoryCanvas(),
          editable: false,
          readOnly: true,
        }),
        expect: '十月底上线新版搜索',
      },
    ],
  },
  {
    id: 'canvas-block-view',
    title: 'CanvasBlockView',
    about: '画布块的节点视图：标题和一句说明（能改时就地改），下面是画布，底边拖高，右上角全屏。',
    file: 'src/components/panels/doc/blocks/CanvasBlockView.vue',
    component: CanvasBlockView,
    needs: ['i18n'],
    states: [
      {
        name: '能改',
        note: '标题和说明是两行能直接改的字；没写标题时是「未命名画布」。',
        props: blockViewProps(),
        expect: '十月底上线新版搜索',
      },
      {
        name: '只读',
        note: '标题和说明只是字；底边不能拖。',
        props: blockViewProps({ editable: false }),
        expect: '十月底上线新版搜索',
      },
      {
        name: '还没有画布',
        note: '原文里写着 :::canvas new、又没有人在建它（比如建它的那一页关掉了）：能改的人点「新建画布」。',
        props: blockViewProps({ canvas: '' }),
        expect: '暂无画布',
      },
    ],
  },
  {
    id: 'canvas-board',
    title: 'CanvasBoard',
    about: '画布本身：格子摆在板上，格子之间连线。没摆过位置的格子按连线从左到右排。',
    file: 'src/components/canvas/CanvasBoard.vue',
    component: CanvasBoard,
    needs: ['i18n'],
    args: { apply: async () => {} },
    states: [
      {
        name: '项目地图',
        note: '一个目标，三件事和它们之间的关系，一张便签；两个格子关联了任务。',
        props: { board: MAP, editable: true },
        expect: '定相关度的排序规则',
      },
      {
        name: '只读',
        note: '能拖着看、能缩放；格子拖不动，没有「添加格子」。',
        props: { board: MAP, editable: false },
        expect: '定相关度的排序规则',
      },
      {
        name: '空画布',
        note: '还没有格子：中间一行「暂无格子」，右下角「添加格子」。',
        props: { board: emptyBoard(), editable: true },
        expect: '暂无格子',
      },
    ],
  },
  {
    id: 'canvas-card-node',
    title: 'CanvasCardNode',
    about: '画布上的一个格子：一段 Markdown；关联了任务的，顶上是任务的状态圆点、标题和那一句。',
    file: 'src/components/canvas/CanvasCardNode.vue',
    component: CanvasCardNode,
    needs: ['i18n'],
    states: [
      {
        name: '一段字',
        note: '格子里的 Markdown 照文档的样子画，缩进的列表就是分层。',
        props: { card: GOAL, editable: true },
        expect: '十月底上线新版搜索',
      },
      {
        name: '关联了任务',
        note: '圆点和任务列表里那一格是同一个：待处理是实心暖色，进行中是绿色空心。',
        props: { card: UI_CARD, editable: true, selected: true },
        expect: '搜索结果页改版',
      },
      {
        name: '改字',
        note: '双击之后：格子变成一块输入区，点到别处或按 ⌘↵ 存下，Esc 放弃。',
        props: { card: INDEX, editable: true, editing: true },
        expectSelector: 'textarea.canvas-card__input',
      },
      {
        name: '看不到的任务',
        note: '关联的任务在读的人进不去的频道里：只说有一个，不说是哪个。',
        props: { card: { ...GOAL, task: null, task_hidden: true }, editable: false },
        expect: '你看不到这个任务',
      },
    ],
  },
]
