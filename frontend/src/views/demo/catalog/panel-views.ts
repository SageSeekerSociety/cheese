/**
 * 工作面板那三格自己的内容：改动（`PanelChangesView`）、预览
 * （`PanelPreviewView`）、概览里的文档（`PanelDocView`）。
 *
 * 三格都是「给宿主塞进面板里」的正文，样例参数（改动的树、预览的文件、协同文档的
 * 会话）在 `../catalogFixtures.ts`；文档那一格的头像配色走 `avatarColor`。
 */
import type { CatalogEntry } from './types'

import { avatarColor } from '@/utils/avatar'

import {
  AGENT_NAME,
  CHANGES_EMPTY,
  changesPanelProps,
  docPanelProps,
  docSession,
  NO_REPO,
  PREVIEW_EMPTY,
  previewPanelProps,
} from '../catalogFixtures'
import { previewCommentsBundle } from '../catalogPanelsPreviewFixtures'

import { UI } from './needs'

import PanelChangesView from '@/components/panels/PanelChangesView.vue'
import PanelDocView from '@/components/panels/PanelDocView.vue'
import PanelPreviewView from '@/components/panels/PanelPreviewView.vue'

/** 一个跑着、页面里抛过两个错的帧：给「页面自己抛了错」那一格用。 */
const DEMO_APP_FRAME = {
  id: 1,
  name: 'cheese-preview-demo',
  url: 'https://preview.example.test/',
  label: '课程网站',
  mime: 'text/html',
  version: null,
  live: true,
  posted: true,
  bridged: true,
  runtime: 'ready' as const,
  pageErrors: [
    { message: 'Cannot read properties of undefined (reading "phone")', source: 'signup.ts', line: 41 },
    { message: 'POST /api/signup 422 字段 grade 不能为空', source: '', line: 0 },
  ],
}

export const PANEL_VIEW_ENTRIES: CatalogEntry[] = [
  {
    id: 'panel-changes',
    title: 'PanelChangesView',
    about: '改动那一格：一棵标着增删的树，改到的文件一份一段、差异默认收着，单独打开一份可以看全文或编辑。',
    file: 'src/components/panels/PanelChangesView.vue',
    component: PanelChangesView,
    needs: UI,
    states: [
      {
        name: '读改动的时候',
        note: '转圈，不是骨架：这一格等的东西说不准是一份 diff、一个编辑器、一张图，还是「只读 / 二进制」那一句提示 —— 等的是什么形状，它并不知道。',
        props: changesPanelProps({
          loading: true,
          openPath: null,
          openDiff: null,
          openDiffLines: [],
          fileToolReady: false,
        }),
      },
      {
        name: '这一轮什么都没改',
        note: '树上和右边都写「暂无改动」。',
        props: CHANGES_EMPTY,
        expect: '暂无改动',
      },
      {
        name: '全部改动',
        note: '默认那一面：树上每行一个文件（+N −M 标着改了多少），右边每个文件一段连着往下排、每段的差异收着，点段头铺开。顶部那块（这次交付的情况）由宿主塞进 head 插槽，这里没有。',
        props: changesPanelProps({ openPath: null, openDiff: null, openDiffLines: [] }),
        expect: '3 个文件',
      },
      {
        name: '单独打开一份',
        note: '点段头的「打开」：只看这一份，横条上有返回、差异和全文两面。',
        props: changesPanelProps(),
        expect: '这个项目放本课程的课件和作业',
      },
      {
        name: '保存冲突',
        note: '你编辑期间芝士又改了同一份文件：两个版本都留着，由人按一下决定谁赢 —— 静默替人选一个，就是改动消失的方式。',
        props: changesPanelProps({ fileConflict: true, fileDirty: true }),
        expect: '你编辑期间，这个文件已被修改',
      },
      {
        name: '没绑仓库',
        note: '这个项目没有代码仓库：一句话说清，不画一棵空树。',
        props: changesPanelProps(NO_REPO),
        expect: '暂无代码仓库',
      },
      {
        name: '读不到改动',
        note: '取 diff 失败：说清失败的是什么，还留着「回到已提交版本」这条出路。',
        props: changesPanelProps({ errorMsg: '拉取改动失败：请求超时', fileSource: 'live' }),
        expect: '拉取改动失败',
      },
    ],
  },
  {
    id: 'panel-preview',
    title: 'PanelPreviewView',
    about: '预览那一格：芝士最后摆出来的那一样 —— 一篇文档、一张表、一个跑着的应用，或者一句「还没有」。',
    file: 'src/components/panels/PanelPreviewView.vue',
    component: PanelPreviewView,
    needs: UI,
    states: [
      {
        name: '读预览的时候',
        note: '转圈，不是骨架：底下那一格可能是网页、文档、表格、图片，等完才知道是哪一样。',
        props: previewPanelProps({ loading: true }),
      },
      {
        name: '还没有东西可看',
        note: '房间里还没摆出过任何东西：一句话，不是一块空白。',
        props: PREVIEW_EMPTY,
        expect: '暂无预览',
      },
      {
        name: '一篇 markdown',
        note: '正文直接画出来（和聊天用的是同一个渲染器）：标题、清单都在，顶上是文件名和类型。',
        props: previewPanelProps(),
        expect: '课件和作业都在这里',
      },
      {
        name: '网页太大，只摆了开头',
        note: '一份超过读入上限的网页：切到「看原文」时，底下说清只摆了开头、整份在下载里。以前这一档什么都不说，读者滑到 `</html>` 会以为文件就这么长。判据是 `too_large`，和代码文件那一档同一个。',
        props: previewPanelProps({
          previewFile: {
            path: 'site/index.html',
            content: null,
            head: '<!doctype html>\n<html>\n<head><title>课程网站</title></head>\n<body>\n  <h1>本周作业</h1>\n  <p>1. 读完第二章</p>\n',
            version: 'v1',
            bytes: 8_000_000,
            binary: false,
            too_large: true,
          },
          previewMime: 'text/html',
          previewNamed: true,
          previewUrl: 'https://preview.example.test/site/index.html',
          previewNamedPath: 'site/index.html',
        }),
        // 那条说明在「看原文」里：这一格先点进去，再看它。
        click: 'toggle-frame-source',
        expect: '只摆了开头',
      },
      {
        name: '这一份读不到了',
        note: '文件还在清单上、字节取不到：说的是「无法读取文件」，和「预览加载失败」不是同一件事。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewNamed: false,
          previewReadError: '这个文件已经不在了',
        }),
        expect: '无法读取文件',
      },
      {
        name: '第一次连上，还在等',
        note: '冷启动最开始那一段：还不知道摆的是不是应用（`authorizing`），内容区摆的是页面大概的样子——一根标题、几根正文条、一块——不是一片白。顶上那行写着已经等了几秒、慢在哪。它只在**真没有内容可看**时摆：换一页时上一帧还在屏幕上，那时不摆；已经认出是应用但睡着了，那是休眠卡的事。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: 'text/html',
          previewNamed: true,
          previewUrl: null,
          previewAppNote: '课程网站',
          previewNamedPath: 'app',
          // 通道和应用都在（不然这一档会走休眠卡），只是第一帧还在拿授权。
          previewTunnelUp: true,
          previewAppUp: true,
          navigation: 'authorizing',
        }),
        expect: '正在获取预览授权',
        // 那几根占位条是这一格的重点，光钉那句字面钉不住它。
        expectSelector: '[data-testid=preview-skeleton]',
      },
      {
        name: '跑着的应用断了',
        note: '机器休眠和应用自己没了是两张不同的卡：这里是第一张，还没留过快照时卡下面垫浅底。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewNamed: true,
          previewAppNote: '课程网站',
          previewNamedPath: 'app',
          previewTunnelUp: false,
        }),
        expect: '运行环境已休眠',
      },
      {
        name: '断着，但有一份快照',
        note: '最常见的那一种：应用睡着，卡下面压着最近一次加载成功的页面（0 沙箱开销），唤醒按钮就在卡上。底下那行写的是快照**停在哪一页**——一份应用只留一张快照，几个读者同开时后写的盖掉先写的，不说清就会让人以为自己那一页被冻住了。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewNamed: true,
          previewAppNote: '课程网站',
          previewNamedPath: 'app',
          previewTunnelUp: false,
          appSnapshot: {
            html: '<!doctype html><html><body><p>快照</p></body></html>',
            age: '4 分钟前',
            where: '提交确认',
          },
        }),
        expect: '停在「提交确认」',
      },
      {
        name: '页面自己抛了错',
        note: '应用照常显示、连通链三个绿勾，但页面里的脚本抛了异常：条上出一颗红的「2 个错误」，点开是报错清单，能把这几行一次附进批注。以前这一档只认「应用说它 ready / failed」，这种页面读者只能干瞪眼。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: 'text/html',
          previewNamed: true,
          previewUrl: 'https://preview.example.test/',
          previewAppNote: '课程网站',
          previewNamedPath: 'app',
          previewTunnelUp: true,
          previewAppUp: true,
          previewError: null,
          previewReadError: null,
          frames: [DEMO_APP_FRAME],
          displayedFrame: DEMO_APP_FRAME,
        }),
        expect: '2 个错误',
      },
      {
        name: '取预览失败',
        note: '读这一格本身失败：一句「预览加载失败」，底下一行是这个错。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewError: '接口返回 502',
        }),
        expect: '预览加载失败',
      },
      {
        name: '这一份文件上的评论',
        note: '点那颗「评论」开出来的那一栏：盖在右边，每张卡写作者的名字和头像（名册那一包给的，和文档那一栏同一处），右上角那颗 × 收起它 —— 开它的按钮就在栏底下，没有这一颗就没有出口。',
        props: previewPanelProps({ comments: previewCommentsBundle({ open: true }) }),
        // 这一格断的是「接名册」那一段：名字写的是名册上的名字，接线退回占位值时卡上
        // 会变成 handle（`li`、`wang`），这里当场红。头像那一路由
        // `preview/PreviewComments.spec.ts` 单测盯着（同样做过变异验证）。
        expect: '李甘',
        expectSelector: '.file-comments__close',
      },
    ],
  },
  {
    id: 'panel-doc',
    title: 'PanelDocView',
    about: '概览那一格的文档：工具条、正文和评论侧栏，窄面板中评论以抽屉展开。',
    file: 'src/components/panels/PanelDocView.vue',
    component: PanelDocView,
    needs: UI,
    states: [
      {
        name: '没有话题',
        note: '这一格属于一个话题；没有话题时说的话和「文档是空的」不一样。',
        props: docPanelProps({ topic: null }),
        expect: '选择一个频道查看文档',
      },
      {
        name: '文档还在路上的时候',
        note: '加载期间摆的是骨架，编辑器让位 —— 空编辑器会亮出「芝士会在这里维护文档」，那句话的意思是「这篇是空的」，而它还没到。',
        props: docPanelProps({ loading: true }),
      },
      {
        name: '一篇文档',
        note: '标题（话题名）、工具条、正文和评论入口都在。正文是一份协同文档，几个人同时打开时横条上是他们的头像。',
        props: docPanelProps({
          session: docSession(
            '## 课程资料\n\n每周一更新一次，作业在这里登记。\n\n- 第一周：读书报告\n- 第二周：小组讨论'
          ),
          peers: [
            {
              clientId: 2,
              handle: 'li-laoshi',
              agent: false,
              name: '李老师',
              avatar: '',
              color: avatarColor('li-laoshi'),
            },
            {
              clientId: 3,
              handle: 'cheese-demo',
              agent: true,
              name: AGENT_NAME,
              avatar: '',
              color: avatarColor('cheese-demo'),
            },
          ],
        }),
        expect: '课程资料',
      },
      {
        name: '没连上',
        note: '断开时改动留在本地，连上后自动合并：顶栏上说的是状态，不是错误。',
        props: docPanelProps({ session: docSession('还没同步的改动也在这里。'), connection: 'offline' }),
        expect: '没连上，改动会在连上后同步',
      },
      {
        name: '没有编辑权限',
        note: '只读由凭证决定：横条上写着只读，点不回编辑，⋯ 里也没有那一项。',
        props: docPanelProps({ session: docSession('只能看，不能改。'), editable: false, readOnly: true }),
        expect: '只读',
      },
    ],
  },
]
