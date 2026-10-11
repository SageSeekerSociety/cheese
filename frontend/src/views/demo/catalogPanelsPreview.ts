/**
 * `panels/preview/` 这一册：预览面板拆出来的那些零件 —— 网页/幻灯片/表格的视图、
 * 设计图与标注画布、定位框、修订清单、保存历史、在线编辑器……
 *
 * 单独一份，和 `catalogPanelsDoc.ts` 同一个理由：`catalog.ts` 已经九百行上下、接近 `frontend/src` 的文件规模上限；
 * 这一册从 `catalogPanels.ts` spread 进去。样例数据在
 * `catalogPanelsPreviewFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  fileEditorUnsupported,
  fileHistoryWith,
  PREVIEW_IMAGE_SRC,
  previewCsvBytes,
  revisionsWith,
  SKETCH_PEN,
  SKETCH_RECT,
} from './catalogPanelsPreviewFixtures'

import DesignImage from '@/components/panels/preview/DesignImage.vue'
import DesignRasterRegion from '@/components/panels/preview/DesignRasterRegion.vue'
import DesignRegionNote from '@/components/panels/preview/DesignRegionNote.vue'
import DesignSketchCanvas from '@/components/panels/preview/DesignSketchCanvas.vue'
import DesignSketchOverlay from '@/components/panels/preview/DesignSketchOverlay.vue'
import DesignSketchToolbar from '@/components/panels/preview/DesignSketchToolbar.vue'
import DesignViewportToolbar from '@/components/panels/preview/DesignViewportToolbar.vue'
import PreviewLocator from '@/components/panels/preview/PreviewLocator.vue'
import PreviewMarkdown from '@/components/panels/preview/PreviewMarkdown.vue'
import PreviewPage from '@/components/panels/preview/PreviewPage.vue'
import PreviewPages from '@/components/panels/preview/PreviewPages.vue'
import PreviewPickToggle from '@/components/panels/preview/PreviewPickToggle.vue'
import PreviewRegionPick from '@/components/panels/preview/PreviewRegionPick.vue'
import PreviewSheet from '@/components/panels/preview/PreviewSheet.vue'
import PreviewSlides from '@/components/panels/preview/PreviewSlides.vue'
import RedactStyleSwatch from '@/components/panels/preview/RedactStyleSwatch.vue'
import RevisionList from '@/components/panels/preview/RevisionList.vue'
import RoomFileEditor from '@/components/panels/preview/RoomFileEditor.vue'
import RoomFileHistory from '@/components/panels/preview/RoomFileHistory.vue'
import SlideThumbRail from '@/components/panels/preview/SlideThumbRail.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n']
const T: CatalogNeed[] = ['i18n']

const MARKDOWN_SAMPLE = ['# 一份说明', '', '这一页把 Markdown 按文档的读法读出来。'].join('\n')

export const PREVIEW_PANEL_ENTRIES: CatalogEntry[] = [
  {
    id: 'preview-panel-page',
    title: 'PreviewPage · 网页视图',
    about: '网页预览：把后端渲染好的那一页交给一个沙箱 iframe（只给 allow-scripts，不给同源），换一页就是换 srcdoc。',
    file: 'src/components/panels/preview/PreviewPage.vue',
    component: PreviewPage,
    needs: T,
    states: [
      {
        name: '一页网页',
        note: '后端渲染好的 html 交给一帧：父页面的 CSP 会被它继承，脚本能跑但网络由页面自带的 CSP 关着。',
        props: { html: '<p>示例页面</p>' },
        expectSelector: 'iframe.page-frame',
      },
    ],
  },
  {
    id: 'preview-panel-markdown',
    title: 'PreviewMarkdown · Markdown 预览',
    about: 'Markdown 的阅读视图：按文档的读法渲染（块、图表、公式和文档里一样），同时让读者指着一句话发出引用。',
    file: 'src/components/panels/preview/PreviewMarkdown.vue',
    component: PreviewMarkdown,
    needs: T,
    states: [
      {
        name: '一段 markdown',
        note: '文件里的单个换行是软换行；链接统一新开一页。',
        props: { source: MARKDOWN_SAMPLE },
        expect: '这一页把 Markdown 按文档的读法读出来',
      },
    ],
  },
  {
    id: 'preview-panel-slides',
    title: 'PreviewSlides · 幻灯片',
    about: '幻灯片阅读器：顶上一条工具栏（缩略图、上一页 / 下一页、跳转、适合页面、演示、下载），中间是当前那一页。',
    file: 'src/components/panels/preview/PreviewSlides.vue',
    component: PreviewSlides,
    needs: UI,
    states: [
      {
        name: '还没有幻灯片',
        note: '字节还在路上或还没有：给一句空态。',
        props: { data: null },
        expect: '暂无幻灯片',
      },
      {
        name: '转不出来',
        note: '这份 PDF 转不成幻灯片：说一声「暂时无法转换预览，可下载原文件」。',
        props: { data: null, rendererMissing: true },
        expect: '暂时无法转换预览',
      },
    ],
  },
  {
    id: 'preview-panel-pages',
    title: 'PreviewPages · 分页视图',
    about: '分页视图：把 PDF 一页页画出来，滚到眼前才画；点一下可以「指位置」，选中文字可以引用。',
    file: 'src/components/panels/preview/PreviewPages.vue',
    component: PreviewPages,
    needs: UI,
    states: [
      {
        name: '等着指位置',
        note: '工具栏上有「指位置」那一颗；这一份没带上已验证的版本，所以它说得出「先验证版本」而按不动。',
        props: { data: null },
        expect: '指位置',
      },
    ],
  },
  {
    id: 'preview-panel-sheet',
    title: 'PreviewSheet · 表格视图',
    about: '表格预览：CSV / TSV 和工作簿摆成一张表，行列有表头，能切看原文。',
    file: 'src/components/panels/preview/PreviewSheet.vue',
    component: PreviewSheet,
    needs: UI,
    states: [
      {
        name: '一份 CSV',
        note: '逗号分隔的文本摆成表格，第一行当表头。',
        props: { data: previewCsvBytes(), kind: 'csv' },
        expect: '张三',
      },
    ],
  },
  {
    id: 'preview-panel-thumb-rail',
    title: 'SlideThumbRail · 缩略图那一列',
    about: '幻灯片旁边那一列缩略图：一页一张，点了跳过去；滚到眼前才画那一页。',
    file: 'src/components/panels/preview/SlideThumbRail.vue',
    component: SlideThumbRail,
    needs: T,
    states: [
      {
        name: '一叠缩略图',
        note: '五页，当前在第 1 页：一页一个按钮，下面写着页码。',
        props: { count: 5, current: 1, revision: 1, paint: async () => {}, cancelPaint: () => {} },
        expect: '1',
      },
    ],
  },
  {
    id: 'preview-panel-locator',
    title: 'PreviewLocator · 指出一处',
    about: '「指位置」以后冒出来的那一条：写一句要改什么，回车或点发送交出去。',
    file: 'src/components/panels/preview/PreviewLocator.vue',
    component: PreviewLocator,
    needs: UI,
    states: [
      {
        name: '指了一处',
        note: '上面写着指的是哪儿（标签 + 那句话），下面一个输入框写要改什么。',
        props: { target: { label: '网页 · 第 2 屏', quote: '这块按钮点不动' }, note: '' },
        expect: '网页 · 第 2 屏',
      },
    ],
  },
  {
    id: 'preview-panel-pick-toggle',
    title: 'PreviewPickToggle · 圈选开关',
    about: '预览条上的「圈选」开关：只在网页预览上给这一颗，按下去就能在页面上拖出一块。',
    file: 'src/components/panels/preview/PreviewPickToggle.vue',
    component: PreviewPickToggle,
    needs: UI,
    states: [
      {
        name: '开着',
        note: '开着时按钮发亮（给的是图标，字在 title 里），再点一下关掉。',
        props: { on: true },
        expectSelector: '[data-testid="preview-pick"][aria-pressed="true"]',
      },
      {
        name: '关着',
        note: '关着时是默认的样子。',
        props: { on: false },
        expectSelector: '[data-testid="preview-pick"][aria-pressed="false"]',
      },
    ],
  },
  {
    id: 'preview-panel-region-pick',
    title: 'PreviewRegionPick · 圈选那一层',
    about: '应用那一档的圈选：应用没有桥读不到 DOM，就在 iframe 上盖一层透明遮罩，读者拖出一个矩形。',
    file: 'src/components/panels/preview/PreviewRegionPick.vue',
    component: PreviewRegionPick,
    needs: [],
    states: [
      {
        name: '盖在页面上',
        note: '平时是一层看不见的透明遮罩：按下、拖、抬手，拖出来的那块会带几何和当时的视口发上去。太小的一下（多半是点了一下）不报。',
        props: {},
        expectSelector: '[data-testid="preview-region-pick"]',
      },
    ],
  },
  {
    id: 'preview-panel-viewport-toolbar',
    title: 'DesignViewportToolbar · 视口工具',
    about: '设计预览的视口工具：选桌面 / 平板 / 手机，缩小 / 放大，显示比例，适合视口。',
    file: 'src/components/panels/preview/DesignViewportToolbar.vue',
    component: DesignViewportToolbar,
    needs: T,
    states: [
      {
        name: '桌面',
        note: '选的是桌面、比例 100%。',
        props: { device: 'desktop', scale: 1, fitted: false },
        expect: '100%',
      },
      {
        name: '缩了一半',
        note: '比例 50%：数字跟着变。',
        props: { device: 'mobile', scale: 0.5, fitted: false },
        expect: '50%',
      },
    ],
  },
  {
    id: 'preview-panel-redact-swatch',
    title: 'RedactStyleSwatch · 涂黑样式小图',
    about:
      '涂黑样式的小预览图（工具栏那一排）：实心一颗黑块，马赛克 3×3，噪点 7×7 —— 灰格由固定的式子推出来，每次渲染一样。',
    file: 'src/components/panels/preview/RedactStyleSwatch.vue',
    component: RedactStyleSwatch,
    needs: [],
    states: [
      {
        name: '实心',
        note: '一颗黑块。',
        props: { style: 'solid' },
        expectSelector: 'svg.redact-swatch rect:only-child',
      },
      {
        name: '马赛克',
        note: '3×3 的灰格。',
        props: { style: 'mosaic' },
        expectSelector: 'svg.redact-swatch rect:nth-child(9):last-child',
      },
      {
        name: '噪点',
        note: '7×7 的灰格。',
        props: { style: 'noise' },
        expectSelector: 'svg.redact-swatch rect:nth-child(49):last-child',
      },
    ],
  },
  {
    id: 'preview-panel-sketch-toolbar',
    title: 'DesignSketchToolbar · 标注工具那一排',
    about:
      '图片标注的工具排：选择区域、画笔 / 直线 / 箭头 / 矩形 / 椭圆 / 文字 / 涂黑、颜色、撤销重做清空、写一句要改什么、加入对话。',
    file: 'src/components/panels/preview/DesignSketchToolbar.vue',
    component: DesignSketchToolbar,
    needs: T,
    states: [
      {
        name: '画笔',
        note: '用画笔：颜色轮摆出来，有笔画时右边出现「说一句要改什么」和「加入对话」。',
        props: { tool: 'pen', color: '#E03131', canUndo: true, canRedo: false, hasStrokes: true, canSend: false },
        expectSelector: '.sketch-toolbar__note',
      },
      {
        name: '涂黑',
        note: '用涂黑工具（或选中的是一块涂黑）：颜色轮换成涂黑样式那一行。',
        props: {
          tool: 'redact',
          color: '#1F1E1D',
          canUndo: false,
          canRedo: false,
          hasStrokes: false,
          canSend: false,
          redact: true,
          redactStyle: 'mosaic',
        },
        expectSelector: '.sketch-toolbar__styles svg.redact-swatch',
      },
    ],
  },
  {
    id: 'preview-panel-sketch-overlay',
    title: 'DesignSketchOverlay · 标注那一层',
    about: '把屏上已有的标注画在一层 SVG 上（选中 / 编辑的条上带把手）：画笔、直线、箭头、矩形、椭圆、文字、涂黑。',
    file: 'src/components/panels/preview/DesignSketchOverlay.vue',
    component: DesignSketchOverlay,
    needs: [],
    states: [
      {
        name: '画过的几笔',
        note: '一条自由画笔和一块矩形标注，按比例摆回图上。',
        props: { strokes: [SKETCH_PEN, SKETCH_RECT], scale: 1, naturalWidth: 480 },
        expectSelector: 'svg.sketch-overlay',
      },
    ],
  },
  {
    id: 'preview-panel-sketch-canvas',
    title: 'DesignSketchCanvas · 标注画布',
    about: '图片标注的画布：鼠标在这一层上拖出笔画、点出文字，落在图上的坐标按原图像素记。',
    file: 'src/components/panels/preview/DesignSketchCanvas.vue',
    component: DesignSketchCanvas,
    needs: T,
    states: [
      {
        name: '落在图上',
        note: '一层盖在图上、按比例对位的画布；拖着画一笔，抬笔时把这一笔（原图像素）发上去。',
        props: { image: null, identity: 'demo.png', tool: 'pen', color: '#E03131', width: 4 },
        expectSelector: '.sketch-layer',
      },
    ],
  },
  {
    id: 'preview-panel-raster-region',
    title: 'DesignRasterRegion · 框一块区域',
    about: '在图片上框出一块区域：拖一个矩形，或者有内容分界线时拖动吸附到块的边、单击框住整块。',
    file: 'src/components/panels/preview/DesignRasterRegion.vue',
    component: DesignRasterRegion,
    needs: T,
    states: [
      {
        name: '能框',
        note: '开着：鼠标底下那一块会先描出来「点下去会框住这里」。',
        props: { image: null, enabled: true, identity: 'demo.png' },
        expectSelector: '.raster-region',
      },
    ],
  },
  {
    id: 'preview-panel-region-note',
    title: 'DesignRegionNote · 区域的说明',
    about: '指着图片一块区域时冒出来的那张小卡：写着指的是哪儿，下面写一句要改什么，发送或取消。',
    file: 'src/components/panels/preview/DesignRegionNote.vue',
    component: DesignRegionNote,
    needs: T,
    states: [
      {
        name: '指着这一块',
        note: '上面写着指的哪儿（标签 + 那句话），下面输入框和「发送」。区域在视图外时会说一声。',
        props: {
          target: { label: '图片 #3 · 右上角', quote: '这块按钮' },
          note: '',
          geometry: null,
          resourceKey: 'demo-region',
          focusOrigin: null,
          restoreFocus: () => {},
        },
        expect: '图片 #3 · 右上角',
      },
    ],
  },
  {
    id: 'preview-panel-design-image',
    title: 'DesignImage · 设计图',
    about: '设计图预览：一张图铺在视口里，能缩能移、能框选区域、能开标注层，比例和「适合视口」摆在上面。',
    file: 'src/components/panels/preview/DesignImage.vue',
    component: DesignImage,
    needs: T,
    states: [
      {
        name: '一张图',
        note: '上面是缩放、比例和「适合视口」；图铺在下面，能滚轮缩放和空格平移。',
        props: { src: PREVIEW_IMAGE_SRC, alt: '示例图', identity: 'demo.png' },
        expect: '适合视口',
      },
    ],
  },
  {
    id: 'preview-panel-revision-list',
    title: 'RevisionList · 修订清单',
    about: '文档的修订清单：几处待处理（换 / 加 / 删），能一处一处接受或拒绝，也能一次全部处理；只读的版本上不给处理。',
    file: 'src/components/panels/preview/RevisionList.vue',
    component: RevisionList,
    needs: UI,
    states: [
      {
        name: '有三处修订',
        note: '上面写着「修订 3 处」，下面每一行说清改的是哪一段、怎么改的。',
        props: { revs: revisionsWith() },
        expect: '把「30 天」改成「60 天」',
      },
    ],
  },
  {
    id: 'preview-panel-room-file-history',
    title: 'RoomFileHistory · 保存历史',
    about: '文件在频道里的保存历史：每次保存留一版，可以下载或恢复任何一版，当前那一版标出来。',
    file: 'src/components/panels/preview/RoomFileHistory.vue',
    component: RoomFileHistory,
    needs: UI,
    states: [
      {
        name: '有过几次保存',
        note: '最新那一版标着「当前」，每一版写着是第几版、谁存的、什么来源。',
        props: { fileHistory: fileHistoryWith() },
        expect: '第 3 版',
      },
    ],
  },
  {
    id: 'preview-panel-room-file-editor',
    title: 'RoomFileEditor · 在线编辑器',
    about: '频道里的文件在线编辑：顶栏是文件名、只读 / 未保存 / 已存几版，右边是保存历史、关闭；下面嵌着编辑器。',
    file: 'src/components/panels/preview/RoomFileEditor.vue',
    component: RoomFileEditor,
    needs: UI,
    states: [
      {
        name: '这份不能在线编辑',
        note: '会话没开、原因是「这种文件不能在线编辑」：说一声，别摆一个打不开的编辑器。',
        props: { editor: fileEditorUnsupported(), fileHistory: fileHistoryWith(), path: '示例.pdf' },
        expect: '这种文件不能在线编辑',
      },
    ],
  },
]
