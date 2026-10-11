/**
 * 一个频道的文件那一页（`ChannelFilesView`）在预览站里的条目。
 *
 * 这一件以前要一整个频道才看得出来（真房间、真文件区、路由、外壳），现在只吃
 * props：一份文件清单、一个「截断了」的旗子、三种状态（读着的、读回来的、读失败），
 * 和一个由调用方给的 `read`（字节怎么来不是这一件的事）。规矩见 `catalog.ts`，单放
 * 一份是因为 `catalog.ts` 顶着一千行的上限。
 *
 * 数据编在这里而不是 `catalogFixtures.ts`：只有这一件用得上，且要摆出产品里真会
 * 出现的那几种形状——定时任务交回的结果（带日期戳的一层文件夹）、人贴进来的截图、
 * 芝士交付的文档。`CatalogEntry` 是 type-only 引用，运行时不构成循环。
 */
import type { RoomFile } from '@/api/roomFiles'
import type { CatalogEntry, CatalogNeed } from './catalog'

import ChannelFilesView from '@/views/workspace/ChannelFilesView.vue'

/** 页头是 `AppPage`（它的返回链接要路由），正文的字走 `t`。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 字节由调用方给：预览站上给一份写得进 `FileBytesPreview` 的文本。 */
const read = async () => new TextEncoder().encode('这个频道的一份文件。').buffer

const FILES: RoomFile[] = [
  {
    path: '2026-09-14/weekly-report.md',
    bytes: 4210,
    modified_at: '2026-09-14T02:10:00Z',
  },
  {
    path: 'screenshot-2026-09-12.png',
    bytes: 88_460,
    modified_at: '2026-09-12T07:31:00Z',
  },
  {
    path: '采购清单.xlsx',
    bytes: 12_300,
    modified_at: '2026-09-08T09:00:00Z',
  },
]

export const CHANNEL_FILES_ENTRIES: CatalogEntry[] = [
  {
    id: 'channel-files-view',
    title: 'ChannelFilesView',
    about:
      '一个频道自己的文件：贴进来的截图、芝士交付的文档、定时任务交回的结果都在这一页上，最近改的在最前；点一份在右边一栏看内容。',
    file: 'src/views/workspace/ChannelFilesView.vue',
    component: ChannelFilesView,
    needs: UI_T_R,
    args: { read, loading: false, error: null, truncated: false },
    states: [
      {
        name: '有几份文件',
        note: '一行一份：类型图标、名字、改动时间和大小；在文件夹里的一份把文件夹写在下面一行。',
        props: { files: FILES },
        expect: 'screenshot-2026-09-12.png',
      },
      {
        name: '一份都没有',
        note: '这个频道还没往文件区里放过东西：一句「暂无文件」，不摆搜索框也不摆筛选。',
        props: { files: [] },
        expect: '暂无文件',
      },
      {
        name: '读着',
        note: '第一次列还没回来：一个转圈，清单和空态都不画。',
        props: { files: [], loading: true },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '读失败',
        note: '列不出来时原地给一条重试的路（往外 emit retry），不装成「这个频道是空的」。',
        props: { files: [], error: '加载文件失败：网络错误' },
        expect: '无法读取文件',
      },
    ],
  },
]
