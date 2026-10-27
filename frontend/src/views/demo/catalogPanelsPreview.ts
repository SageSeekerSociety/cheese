/**
 * `panels/preview/` 这一册：预览面板拆出来的那些零件 —— 网页/幻灯片/表格的视图、
 * 设计图与标注画布、定位框、修订清单、保存历史、在线编辑器……
 *
 * 单独一份，和 `catalogPanelsDoc.ts` 同一个理由：`catalog.ts` 已经九百行上下、接近 `frontend/src` 的文件规模上限；
 * 这一册从 `catalogPanels.ts` spread 进去。样例数据在
 * `catalogPanelsPreviewFixtures.ts`。
 *
 * 不列的两件：标注画布（`DesignSketchCanvas`）与框选区域（`DesignRasterRegion`）要一张
 * 已加载的图片和它的定位容器才画得出绘画/框选，本批还没为它们构造那样的夹具，空壳那一格
 * 暂不登记（日后补上夹具即可加回）。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  docStageProps,
  fileEditorUnsupported,
  fileHistoryWith,
  frameStageProps,
  headBarProps,
  imageRegionWith,
  imageStageProps,
  PREVIEW_DOC_FILE,
  PREVIEW_IMAGE_SRC,
  previewCommentsProps,
  previewCsvBytes,
  previewFrame,
  revisionsWith,
  SKETCH_PEN,
  SKETCH_RECT,
} from './catalogPanelsPreviewFixtures'

import DesignImage from '@/components/panels/preview/DesignImage.vue'
import DesignRegionNote from '@/components/panels/preview/DesignRegionNote.vue'
import DesignSketchOverlay from '@/components/panels/preview/DesignSketchOverlay.vue'
import DesignSketchToolbar from '@/components/panels/preview/DesignSketchToolbar.vue'
import DesignViewportToolbar from '@/components/panels/preview/DesignViewportToolbar.vue'
import PreviewComments from '@/components/panels/preview/PreviewComments.vue'
import PreviewDocStage from '@/components/panels/preview/PreviewDocStage.vue'
import PreviewFrameStage from '@/components/panels/preview/PreviewFrameStage.vue'
import PreviewHeadBar from '@/components/panels/preview/PreviewHeadBar.vue'
import PreviewImageStage from '@/components/panels/preview/PreviewImageStage.vue'
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
import { t } from '@/i18n'
import { DOCUMENT_TYPES } from '@/lib/fileKind'

const UI: CatalogNeed[] = ['vuetify', 'i18n']
const T: CatalogNeed[] = ['i18n']

const MARKDOWN_SAMPLE = ['# 一份说明', '', '这一页把 Markdown 按文档的读法读出来。'].join('\n')
/** 这一份文件上的两条评论指着的那两处（形状就是评论存下的那一样）。 */
const MARKDOWN_ANCHORS = [
  { id: 'c1', quote: { text: '按文档的读法', heading: '一份说明', prefix: '这一页把 Markdown ', suffix: '读出来' } },
  { id: 'c2', quote: { text: 'Markdown', heading: '一份说明', prefix: '这一页把 ', suffix: ' 按文档' } },
]

export const PREVIEW_PANEL_ENTRIES: CatalogEntry[] = [
  {
    id: 'preview-panel-head-bar',
    title: 'PreviewHeadBar · 预览顶栏',
    get about() {
      return t('catalog.previewPanelHeadBar.about')
    },
    file: 'src/components/panels/preview/PreviewHeadBar.vue',
    component: PreviewHeadBar,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelHeadBar.s1.name')
        },
        get note() {
          return t('catalog.previewPanelHeadBar.s1.note')
        },
        props: headBarProps({ previewUrl: 'http://127.0.0.1:4173/', displayedFrame: previewFrame() }),
        expect: '发布网站',
      },
      {
        get name() {
          return t('catalog.previewPanelHeadBar.s2.name')
        },
        get note() {
          return t('catalog.previewPanelHeadBar.s2.note')
        },
        props: headBarProps({ projectId: null, previewUrl: null, isImageArtifact: true, previewFull: true }),
        expectSelector: '.mdi-fullscreen-exit',
      },
    ],
  },
  {
    id: 'preview-panel-frame-stage',
    title: 'PreviewFrameStage · 网页/应用那一档',
    get about() {
      return t('catalog.previewPanelFrameStage.about')
    },
    file: 'src/components/panels/preview/PreviewFrameStage.vue',
    component: PreviewFrameStage,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelFrameStage.s1.name')
        },
        get note() {
          return t('catalog.previewPanelFrameStage.s1.note')
        },
        props: frameStageProps({ displayedFrame: previewFrame() }),
        expect: '运行中的应用',
      },
      {
        get name() {
          return t('catalog.previewPanelFrameStage.s2.name')
        },
        get note() {
          return t('catalog.previewPanelFrameStage.s2.note')
        },
        props: frameStageProps({
          path: 'docs/week-1.md',
          frames: [previewFrame(), previewFrame({ id: 2, label: '预览 · 第二版' })],
          displayedFrame: previewFrame({ id: 2, label: '预览 · 第二版' }),
        }),
        // 指定了文件的那一格：条上不再写文件名（页签上已经写着），认下载那颗。
        expectSelector: '.mdi-download',
      },
      {
        get name() {
          return t('catalog.previewPanelFrameStage.s3.name')
        },
        get note() {
          return t('catalog.previewPanelFrameStage.s3.note')
        },
        props: frameStageProps({ displayedFrame: previewFrame(), previewError: '内容域没有应答' }),
        expect: '内容域没有应答',
      },
      {
        get name() {
          return t('catalog.previewPanelFrameStage.s4.name')
        },
        get note() {
          return t('catalog.previewPanelFrameStage.s4.note')
        },
        props: frameStageProps({ displayedFrame: previewFrame(), pickTarget: 'region', pickOn: true }),
        expectSelector: '[data-testid="preview-region-pick"]',
      },
    ],
  },
  {
    id: 'preview-panel-doc-stage',
    title: 'PreviewDocStage · 文档那一档',
    get about() {
      return t('catalog.previewPanelDocStage.about')
    },
    file: 'src/components/panels/preview/PreviewDocStage.vue',
    component: PreviewDocStage,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelDocStage.s1.name')
        },
        get note() {
          return t('catalog.previewPanelDocStage.s1.note')
        },
        props: docStageProps(),
        expect: '课件和作业都在这里',
      },
      {
        get name() {
          return t('catalog.previewPanelDocStage.s2.name')
        },
        get note() {
          return t('catalog.previewPanelDocStage.s2.note')
        },
        props: docStageProps({
          documentType: DOCUMENT_TYPES.docx,
          documentName: PREVIEW_DOC_FILE.path,
          documentSuffix: 'docx',
          previewFile: PREVIEW_DOC_FILE,
          docRendererMissing: true,
        }),
        expect: '文档预览未启用',
      },
      {
        get name() {
          return t('catalog.previewPanelDocStage.s3.name')
        },
        get note() {
          return t('catalog.previewPanelDocStage.s3.note')
        },
        props: docStageProps({
          documentType: DOCUMENT_TYPES.docx,
          documentName: PREVIEW_DOC_FILE.path,
          documentSuffix: 'docx',
          previewFile: PREVIEW_DOC_FILE,
          docBytes: new ArrayBuffer(8),
          docError: '接口返回 502',
        }),
        expect: '刷新失败，当前显示的是上一次的内容',
      },
      {
        get name() {
          return t('catalog.previewPanelDocStage.s4.name')
        },
        get note() {
          return t('catalog.previewPanelDocStage.s4.note')
        },
        props: docStageProps({
          documentType: DOCUMENT_TYPES.docx,
          documentName: PREVIEW_DOC_FILE.path,
          documentSuffix: 'docx',
          previewFile: PREVIEW_DOC_FILE,
          canPage: true,
          docPage: true,
          docPageHtml: '<p>示例网页</p>',
        }),
        expectSelector: 'iframe.page-frame',
      },
    ],
  },
  {
    id: 'preview-panel-image-stage',
    title: 'PreviewImageStage · 图片那一档',
    get about() {
      return t('catalog.previewPanelImageStage.about')
    },
    file: 'src/components/panels/preview/PreviewImageStage.vue',
    component: PreviewImageStage,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelImageStage.s1.name')
        },
        get note() {
          return t('catalog.previewPanelImageStage.s1.note')
        },
        props: imageStageProps(),
        expect: '在新窗口打开',
      },
      {
        get name() {
          return t('catalog.previewPanelImageStage.s2.name')
        },
        get note() {
          return t('catalog.previewPanelImageStage.s2.note')
        },
        props: imageStageProps({ imageRegion: imageRegionWith({ src: '' }) }),
        expectSelector: '.doc__state',
      },
      {
        get name() {
          return t('catalog.previewPanelImageStage.s3.name')
        },
        get note() {
          return t('catalog.previewPanelImageStage.s3.note')
        },
        props: imageStageProps({ annotateError: '这张图太大，标注合不出来' }),
        expect: '这张图太大，标注合不出来',
      },
    ],
  },
  {
    id: 'preview-panel-page',
    title: 'PreviewPage · 网页视图',
    get about() {
      return t('catalog.previewPanelPage.about')
    },
    file: 'src/components/panels/preview/PreviewPage.vue',
    component: PreviewPage,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelPage.s1.name')
        },
        get note() {
          return t('catalog.previewPanelPage.s1.note')
        },
        props: { html: '<p>示例页面</p>' },
        expectSelector: 'iframe.page-frame',
      },
    ],
  },
  {
    id: 'preview-panel-markdown',
    title: 'PreviewMarkdown · Markdown 预览',
    get about() {
      return t('catalog.previewPanelMarkdown.about')
    },
    file: 'src/components/panels/preview/PreviewMarkdown.vue',
    component: PreviewMarkdown,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelMarkdown.s1.name')
        },
        get note() {
          return t('catalog.previewPanelMarkdown.s1.note')
        },
        props: { source: MARKDOWN_SAMPLE },
        expect: '这一页把 Markdown 按文档的读法读出来',
      },
      {
        get name() {
          return t('catalog.previewPanelMarkdown.s2.name')
        },
        get note() {
          return t('catalog.previewPanelMarkdown.s2.note')
        },
        // 一份有人评过的文件：那两处画着标记，画重一点的是正在看的那一条。
        // 标记不是文字（是巢在那几个字外面的 `<mark>`），所以这一格看的是形状，不是某一句话：
        // 两处都在，且 c2 那一处是重的。只挑重的那一颗的话，「一条评论都没画、只把在看的那条
        // 画出来」也满足它——而那正是这一格要展示的东西少了一半。
        props: { source: MARKDOWN_SAMPLE, anchors: MARKDOWN_ANCHORS, activeId: 'c2' },
        expectSelector:
          'mark.file-comment-mark.is-active[data-file-comment="c2"] + mark.file-comment-mark:not(.is-active)[data-file-comment="c1"]',
      },
    ],
  },
  {
    id: 'preview-panel-slides',
    title: 'PreviewSlides · 幻灯片',
    get about() {
      return t('catalog.previewPanelSlides.about')
    },
    file: 'src/components/panels/preview/PreviewSlides.vue',
    component: PreviewSlides,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelSlides.s1.name')
        },
        get note() {
          return t('catalog.previewPanelSlides.s1.note')
        },
        props: { data: null },
        expect: '暂无幻灯片',
      },
      {
        get name() {
          return t('catalog.previewPanelSlides.s2.name')
        },
        get note() {
          return t('catalog.previewPanelSlides.s2.note')
        },
        props: { data: null, rendererMissing: true },
        expect: '暂时无法转换预览',
      },
    ],
  },
  {
    id: 'preview-panel-pages',
    title: 'PreviewPages · 分页视图',
    get about() {
      return t('catalog.previewPanelPages.about')
    },
    file: 'src/components/panels/preview/PreviewPages.vue',
    component: PreviewPages,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelPages.s1.name')
        },
        get note() {
          return t('catalog.previewPanelPages.s1.note')
        },
        props: { data: null },
        expect: '指位置',
      },
    ],
  },
  {
    id: 'preview-panel-sheet',
    title: 'PreviewSheet · 表格视图',
    get about() {
      return t('catalog.previewPanelSheet.about')
    },
    file: 'src/components/panels/preview/PreviewSheet.vue',
    component: PreviewSheet,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelSheet.s1.name')
        },
        get note() {
          return t('catalog.previewPanelSheet.s1.note')
        },
        props: { data: previewCsvBytes(), kind: 'csv' },
        expect: '张三',
      },
    ],
  },
  {
    id: 'preview-panel-thumb-rail',
    title: 'SlideThumbRail · 缩略图那一列',
    get about() {
      return t('catalog.previewPanelThumbRail.about')
    },
    file: 'src/components/panels/preview/SlideThumbRail.vue',
    component: SlideThumbRail,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelThumbRail.s1.name')
        },
        get note() {
          return t('catalog.previewPanelThumbRail.s1.note')
        },
        props: { count: 5, current: 1, revision: 1, paint: async () => {}, cancelPaint: () => {} },
        expect: '1',
      },
    ],
  },
  {
    id: 'preview-panel-locator',
    title: 'PreviewLocator · 指出一处',
    get about() {
      return t('catalog.previewPanelLocator.about')
    },
    file: 'src/components/panels/preview/PreviewLocator.vue',
    component: PreviewLocator,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelLocator.s1.name')
        },
        get note() {
          return t('catalog.previewPanelLocator.s1.note')
        },
        props: {
          target: { label: '网页 · 第 2 屏', quote: '这块按钮点不动' },
          note: '这一处按不到',
          canComment: true,
        },
        expect: '评论',
      },
      {
        get name() {
          return t('catalog.previewPanelLocator.s2.name')
        },
        get note() {
          return t('catalog.previewPanelLocator.s2.note')
        },
        // 挂不了评论的那一份（资料库原件、消息附件）只该露出「发送」这一条。
        props: {
          target: { label: 'library/报告.pdf', quote: '第 2 页' },
          note: '这一处的数字不对',
          canComment: false,
        },
        expect: '发送',
      },
    ],
  },
  {
    id: 'preview-panel-pick-toggle',
    title: 'PreviewPickToggle · 圈选开关',
    get about() {
      return t('catalog.previewPanelPickToggle.about')
    },
    file: 'src/components/panels/preview/PreviewPickToggle.vue',
    component: PreviewPickToggle,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelPickToggle.s1.name')
        },
        get note() {
          return t('catalog.previewPanelPickToggle.s1.note')
        },
        props: { on: true },
        expectSelector: '[data-testid="preview-pick"][aria-pressed="true"]',
      },
      {
        get name() {
          return t('catalog.previewPanelPickToggle.s2.name')
        },
        get note() {
          return t('catalog.previewPanelPickToggle.s2.note')
        },
        props: { on: false },
        expectSelector: '[data-testid="preview-pick"][aria-pressed="false"]',
      },
    ],
  },
  {
    id: 'preview-panel-region-pick',
    title: 'PreviewRegionPick · 圈选那一层',
    get about() {
      return t('catalog.previewPanelRegionPick.about')
    },
    file: 'src/components/panels/preview/PreviewRegionPick.vue',
    component: PreviewRegionPick,
    needs: [],
    states: [
      {
        get name() {
          return t('catalog.previewPanelRegionPick.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRegionPick.s1.note')
        },
        props: {},
        expectSelector: '[data-testid="preview-region-pick"]',
      },
    ],
  },
  {
    id: 'preview-panel-viewport-toolbar',
    title: 'DesignViewportToolbar · 视口工具',
    get about() {
      return t('catalog.previewPanelViewportToolbar.about')
    },
    file: 'src/components/panels/preview/DesignViewportToolbar.vue',
    component: DesignViewportToolbar,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelViewportToolbar.s1.name')
        },
        get note() {
          return t('catalog.previewPanelViewportToolbar.s1.note')
        },
        props: { device: 'desktop', scale: 1, fitted: false },
        expect: '100%',
      },
      {
        get name() {
          return t('catalog.previewPanelViewportToolbar.s2.name')
        },
        get note() {
          return t('catalog.previewPanelViewportToolbar.s2.note')
        },
        props: { device: 'mobile', scale: 0.5, fitted: false },
        expect: '50%',
      },
    ],
  },
  {
    id: 'preview-panel-redact-swatch',
    title: 'RedactStyleSwatch · 涂黑样式小图',
    get about() {
      return t('catalog.previewPanelRedactSwatch.about')
    },
    file: 'src/components/panels/preview/RedactStyleSwatch.vue',
    component: RedactStyleSwatch,
    needs: [],
    states: [
      {
        get name() {
          return t('catalog.previewPanelRedactSwatch.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRedactSwatch.s1.note')
        },
        props: { style: 'solid' },
        expectSelector: 'svg.redact-swatch rect:only-child',
      },
      {
        get name() {
          return t('catalog.previewPanelRedactSwatch.s2.name')
        },
        get note() {
          return t('catalog.previewPanelRedactSwatch.s2.note')
        },
        props: { style: 'mosaic' },
        expectSelector: 'svg.redact-swatch rect:nth-child(9):last-child',
      },
      {
        get name() {
          return t('catalog.previewPanelRedactSwatch.s3.name')
        },
        get note() {
          return t('catalog.previewPanelRedactSwatch.s3.note')
        },
        props: { style: 'noise' },
        expectSelector: 'svg.redact-swatch rect:nth-child(49):last-child',
      },
    ],
  },
  {
    id: 'preview-panel-sketch-toolbar',
    title: 'DesignSketchToolbar · 标注工具那一排',
    get about() {
      return t('catalog.previewPanelSketchToolbar.about')
    },
    file: 'src/components/panels/preview/DesignSketchToolbar.vue',
    component: DesignSketchToolbar,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelSketchToolbar.s1.name')
        },
        get note() {
          return t('catalog.previewPanelSketchToolbar.s1.note')
        },
        props: { tool: 'pen', color: '#E03131', canUndo: true, canRedo: false, hasStrokes: true, canSend: false },
        expectSelector: '.sketch-toolbar__note',
      },
      {
        get name() {
          return t('catalog.previewPanelSketchToolbar.s2.name')
        },
        get note() {
          return t('catalog.previewPanelSketchToolbar.s2.note')
        },
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
    get about() {
      return t('catalog.previewPanelSketchOverlay.about')
    },
    file: 'src/components/panels/preview/DesignSketchOverlay.vue',
    component: DesignSketchOverlay,
    needs: [],
    states: [
      {
        get name() {
          return t('catalog.previewPanelSketchOverlay.s1.name')
        },
        get note() {
          return t('catalog.previewPanelSketchOverlay.s1.note')
        },
        props: { strokes: [SKETCH_PEN, SKETCH_RECT], scale: 1, naturalWidth: 480 },
        expectSelector: 'svg.sketch-overlay',
      },
    ],
  },
  {
    id: 'preview-panel-region-note',
    title: 'DesignRegionNote · 区域的说明',
    get about() {
      return t('catalog.previewPanelRegionNote.about')
    },
    file: 'src/components/panels/preview/DesignRegionNote.vue',
    component: DesignRegionNote,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelRegionNote.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRegionNote.s1.note')
        },
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
    get about() {
      return t('catalog.previewPanelDesignImage.about')
    },
    file: 'src/components/panels/preview/DesignImage.vue',
    component: DesignImage,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.previewPanelDesignImage.s1.name')
        },
        get note() {
          return t('catalog.previewPanelDesignImage.s1.note')
        },
        props: { src: PREVIEW_IMAGE_SRC, alt: '示例图', identity: 'demo.png' },
        expect: '适合视口',
      },
    ],
  },
  {
    id: 'preview-panel-revision-list',
    title: 'RevisionList · 修订清单',
    get about() {
      return t('catalog.previewPanelRevisionList.about')
    },
    file: 'src/components/panels/preview/RevisionList.vue',
    component: RevisionList,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelRevisionList.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRevisionList.s1.note')
        },
        props: { revs: revisionsWith() },
        expect: '把「30 天」改成「60 天」',
      },
    ],
  },
  {
    id: 'preview-panel-room-file-history',
    title: 'RoomFileHistory · 保存历史',
    get about() {
      return t('catalog.previewPanelRoomFileHistory.about')
    },
    file: 'src/components/panels/preview/RoomFileHistory.vue',
    component: RoomFileHistory,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelRoomFileHistory.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRoomFileHistory.s1.note')
        },
        props: { fileHistory: fileHistoryWith() },
        expect: '第 3 版',
      },
    ],
  },
  {
    id: 'preview-panel-room-file-editor',
    title: 'RoomFileEditor · 在线编辑器',
    get about() {
      return t('catalog.previewPanelRoomFileEditor.about')
    },
    file: 'src/components/panels/preview/RoomFileEditor.vue',
    component: RoomFileEditor,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.previewPanelRoomFileEditor.s1.name')
        },
        get note() {
          return t('catalog.previewPanelRoomFileEditor.s1.note')
        },
        props: { editor: fileEditorUnsupported(), fileHistory: fileHistoryWith(), path: '示例.pdf' },
        expect: '这种文件不能在线编辑',
      },
    ],
  },
  {
    id: 'preview-panel-comments',
    title: 'PreviewComments · 文件上的评论',
    about:
      '一份房间文件上的评论栏：上面写一条新的，下面一串串已有的；每张卡写作者的名字、画他挑过的头像。它是盖住预览右边的浮层，所以右上角那颗 × 是它的关闭入口。',
    file: 'src/components/panels/preview/PreviewComments.vue',
    component: PreviewComments,
    needs: UI,
    states: [
      {
        name: '两串评论',
        note: '人开的头、芝士在答的那一串没有解决，另一串已经解决：卡上写的是名册上的名字（不是 handle），头像按人各归各的 —— 李甘没挑过，画彩色首字母。',
        props: previewCommentsProps(),
        expect: '这里的用时要不要一起写进去',
        expectSelector: '.file-comments__close',
      },
      {
        name: '指着一处再说',
        note: '预览里选中一段再开这一栏：上面那条引用写明评的是哪几个字，旁边那颗 × 撤掉这次指认，栏本身不关。',
        props: previewCommentsProps({ pick: { quote: '搜索次数翻倍。', anchor: null } }),
        expect: '搜索次数翻倍。',
      },
      {
        name: '还没有人评论',
        note: '一份新的文件：一句交代，不是一个空白。',
        props: previewCommentsProps({
          threadState: { threads: [], activity: {}, errors: {}, busy: false, unknown: null },
        }),
        expect: '暂无评论',
      },
    ],
  },
]
