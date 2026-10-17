/**
 * 频道和私聊右侧面板「文件」那一格（`PanelRoomFiles`）、私聊页的两栏（`DmViewView`）
 * 和保存前的确认在预览站里的条目。单放一份是因为
 * `catalog.ts` 顶着一千行的上限。
 *
 * 数据编在这里：只有这一件用得上，且要摆出产品里真会出现的几种形状——芝士写的文档、
 * 人发进来的附件、定时任务交回的结果（带日期戳的一层文件夹）。`author` 只存着 handle，
 * 名字由名册换出来，所以这里也编一份 `names`。
 */
import type { ConversationFile } from '@/composables/useConversationFiles'
import type { CatalogEntry } from './catalog'

import PanelRoomFiles from '@/components/panels/PanelRoomFiles.vue'
import KeepToLibraryConfirm from '@/components/room/KeepToLibraryConfirm.vue'
import PrivateChatFiles from '@/components/room/PrivateChatFiles.vue'
import DmViewView from '@/views/workspace/DmViewView.vue'

/** 预览站上的名册：这一条目录里没有真名册，编一份和房间里同名同形的。 */
const NAMES: Record<string, string> = { alice: '阿丽', 'cheese-1a2b3c': '小苔' }

/** 「文件」那一格（#3828）的几行：一份文档、一个定时任务交回结果的文件夹、一份附件。 */
const CONVERSATION_FILES: ConversationFile[] = [
  {
    kind: 'document',
    id: 'doc-1',
    name: '本周复盘',
    added_by: 'cheese-1a2b3c',
    folder: '',
    added_at: '2026-09-14T02:10:00Z',
    in_library: false,
  },
  {
    kind: 'file',
    id: 'runs/2026-09-14/weekly-report.md',
    name: 'weekly-report.md',
    folder: 'runs/2026-09-14',
    added_by: 'cheese-1a2b3c',
    added_at: '2026-09-14T02:09:00Z',
    in_library: false,
  },
  {
    kind: 'file',
    id: 'uploads/0123456789abcdef0123456789abcdef/采购清单.xlsx',
    name: '采购清单.xlsx',
    folder: '',
    added_by: 'alice',
    added_at: '2026-09-08T09:00:00Z',
    in_library: true,
  },
]

export const CHANNEL_FILES_ENTRIES: CatalogEntry[] = [
  {
    id: 'panel-room-files',
    title: 'PanelRoomFiles',
    about:
      '频道和私聊右侧面板的「文件」：这段对话里发过的附件、文件区里的文件和芝士写的文档，一层一层像文件夹；每一行写着谁、什么时候放进来的，还没在资料库里的给一颗「保存到资料库」。',
    file: 'src/components/panels/PanelRoomFiles.vue',
    component: PanelRoomFiles,
    needs: ['vuetify', 'i18n'],
    args: { names: NAMES, loading: false, error: null, keeping: new Set<string>() },
    states: [
      {
        name: '有几份',
        note: '文件夹在前，其余新的在前。已经保存过的写「已在资料库」，没保存的给按钮。',
        props: { rows: CONVERSATION_FILES },
        expect: '已在资料库',
      },
      {
        name: '一份都没有',
        note: '这段对话里还没发过文件，芝士也没写过文档。',
        props: { rows: [] },
        expect: '暂无文件',
      },
    ],
  },
  {
    id: 'private-chat-files',
    title: 'PrivateChatFiles',
    about: '私聊右侧面板的「文件」：清单和频道那一格一样；点一份文件就在这一栏里看，上面一条返回清单、下载。',
    file: 'src/components/room/PrivateChatFiles.vue',
    component: PrivateChatFiles,
    needs: ['vuetify', 'i18n'],
    args: {
      rows: CONVERSATION_FILES,
      names: NAMES,
      loading: false,
      error: null,
      keeping: new Set<string>(),
      truncated: false,
      read: async () => new TextEncoder().encode('这一周的数据。').buffer,
    },
    states: [
      { name: '清单', note: '和频道那一格同一个清单。', props: { viewing: null }, expect: '已在资料库' },
      {
        name: '看一份文件',
        note: '上面一条：返回清单、文件名、下载；下面是这份文件的内容。',
        props: { viewing: 'runs/2026-09-14/weekly-report.md' },
        expect: 'weekly-report.md',
      },
    ],
  },
  {
    id: 'keep-to-library-confirm',
    title: 'KeepToLibraryConfirm',
    about: '私密频道和私聊里保存到资料库之前的确认：现在几个人看得到，保存后几个人看得到。',
    file: 'src/components/room/KeepToLibraryConfirm.vue',
    component: KeepToLibraryConfirm,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { people: 2, projectPeople: 5 },
    states: [{ name: '问一句', note: '取消就不保存。', props: { name: '合作协议.docx' }, expect: '合作协议.docx' }],
  },
  {
    id: 'dm-view-view',
    title: 'DmViewView',
    about: '私聊页的两栏：左边对话，右边「文件」面板，中间可拖的分隔，可以收起或铺满。对话和面板由页面放进来。',
    file: 'src/views/workspace/DmViewView.vue',
    component: DmViewView,
    needs: ['vuetify', 'i18n'],
    args: {
      error: null,
      ready: true,
      desktop: true,
      docked: true,
      panelOpen: true,
      panelFloat: false,
      focusMode: false,
      chatNews: false,
      focusLabel: '专注模式',
      focusIcon: 'mdi-arrow-expand',
      chatStyle: { flex: '0 0 50%' },
    },
    states: [
      { name: '并排', note: '面板开着，页签栏右边是铺满和收起。', props: {}, expect: '文件' },
      {
        name: '取不到',
        note: '这段私聊打不开时只有一句话。',
        props: { error: '无法打开私聊' },
        expect: '无法打开私聊',
      },
    ],
  },
]
