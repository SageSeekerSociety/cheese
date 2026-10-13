/**
 * 频道那两个弹窗在预览站里的条目。
 *
 * 单独一份是因为 `catalog.ts` 已经九百多行，再塞两条会顶到 `frontend/src` 那一千行
 * 的上限 —— 和 `catalogRoom.ts`、`catalogChannelFiles.ts` 同一个理由。
 *
 * 两件都是后来才有的动作：删频道要先确认，建支线不必先有一条消息可回。它们都只吃
 * props、只往上发一个事件，开出来之后去哪由打开它们的页面决定，所以摆得出来。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要
 * `CHANNEL_DIALOG_ENTRIES` 这个值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import DeleteChannelDialog from '@/components/channel/DeleteChannelDialog.vue'
import NewThreadDialog from '@/components/channel/NewThreadDialog.vue'

/** 两件都要 vuetify（`v-text-field` 和按钮），也都用目录里的字（标题、按钮名）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 摆出来的那个频道叫什么：写在标题里，删的时候也要照着它打。 */
const CHANNEL = '产品设计'

export const CHANNEL_DIALOG_ENTRIES: CatalogEntry[] = [
  {
    id: 'delete-channel-dialog',
    title: 'DeleteChannelDialog',
    about: '删频道前的二次确认：把频道名原样打一遍才放行，挡的是手滑，不是权限。',
    file: 'src/components/channel/DeleteChannelDialog.vue',
    component: DeleteChannelDialog,
    needs: UI_T,
    teleport: true,
    args: { modelValue: true, name: CHANNEL },
    states: [
      {
        name: '刚打开，名字还没打',
        note: '「删除频道」是红色的危险主操作，名字没打对之前一直是灰的。输入框里打的字是框自己的状态，不从 props 来，所以「打对了名字」那一格要在框里自己打一遍。',
        props: {},
        expect: `删除「${CHANNEL}」？`,
        expectSelector: '.v-btn--disabled',
      },
      {
        name: '正在删',
        note: '删的时候主按钮转圈，这时候关不掉。删本身在外面做，这里只在打对名字之后报一声「确认了」。',
        props: { busy: true },
        expectSelector: '.v-btn--loading',
      },
    ],
  },
  {
    id: 'new-thread-dialog',
    title: 'NewThreadDialog',
    about: '在频道下新建一条支线：填一个标题就开出来，不必先在主线上找一条消息去回复。',
    file: 'src/components/channel/NewThreadDialog.vue',
    component: NewThreadDialog,
    needs: UI_T,
    teleport: true,
    args: { modelValue: true, channel: CHANNEL, busy: false },
    states: [
      {
        name: '刚打开，标题还空着',
        note: '标题空着时「新建支线」是灰的 —— 支线要有名字才开得出来；框顶上写着这是在哪一个频道下开。',
        props: {},
        expect: `在「${CHANNEL}」下新建支线`,
        expectSelector: '.v-btn--disabled',
      },
      {
        name: '正在建',
        note: '建的时候主按钮转圈，这时候关不掉；建完去哪由打开它的页面决定。',
        props: { busy: true },
        expectSelector: '.v-btn--loading',
      },
    ],
  },
]
