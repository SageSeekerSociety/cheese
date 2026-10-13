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
  fileEditorUnsupported,
  fileHistoryWith,
  PREVIEW_IMAGE_SRC,
  previewCsvBytes,
  revisionsWith,
  SKETCH_PEN,
  SKETCH_RECT,
} from './catalogPanelsPreviewFixtures'

import DesignImage from '@/components/panels/preview/DesignImage.vue'
import DesignRegionNote from '@/components/panels/preview/DesignRegionNote.vue'
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
import { t } from '@/i18n'

const UI: CatalogNeed[] = ['vuetify', 'i18n']
const T: CatalogNeed[] = ['i18n']

const MARKDOWN_SAMPLE = ['# 一份说明', '', '这一页把 Markdown 按文档的读法读出来。'].join('\n')

export const PREVIEW_PANEL_ENTRIES: CatalogEntry[] = [
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
        props: { target: { label: '网页 · 第 2 屏', quote: '这块按钮点不动' }, note: '' },
        expect: '网页 · 第 2 屏',
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
]
