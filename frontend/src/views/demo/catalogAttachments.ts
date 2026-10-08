/**
 * 附件那两件在预览站里的条目：一条消息里的那张图，和一份文档的第一页。
 *
 * 两件画的都是「取回来的字节」——图要先 fetch 再转 object URL（`<img src>` 带不了
 * Authorization 头），文档的封面要先拿 PDF 字节再画。取字节的那个函数不再 import 在
 * 组件里，而是从外壳注入（`lib/attachmentSource.ts`），所以这两件自己只吃 props。
 * 预览站里没有外壳，注入的是那份「取不到」的空实现 —— 于是这里摆的正是产品里真会走到
 * 的那几格：图取不回来时正文里那一句、待发条上那个断图图标、文档方格里那个类型图标，
 * 没有一格是空白。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和
 * `catalogViews.ts`、`catalogFeedback.ts` 同一个理由。这里的 `CatalogEntry` 是 type-only
 * 引用：`catalog.ts` 反过来要 `ATTACHMENT_ENTRIES` 这个值，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import AttachmentDocThumb from '@/components/AttachmentDocThumb.vue'
import AttachmentImage from '@/components/AttachmentImage.vue'

export const ATTACHMENT_ENTRIES: CatalogEntry[] = [
  {
    id: 'attachment-image',
    title: 'AttachmentImage',
    about:
      '一条图片消息里的那张图：先取字节、转成 object URL 再挂上去，取不回来时说明一句 —— 空白和「这条消息本来就没图」长得一样。',
    file: 'src/components/AttachmentImage.vue',
    component: AttachmentImage,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '图没取回来',
        note: '字节取不到（离线、被撤走、外壳没注入取字节的那一份）时说一句「图片加载失败」，不留一块看不出缘由的空白。',
        props: { topicId: 't-1', path: 'uploads/2026-10-01/photo.png' },
        expect: '图片加载失败',
      },
      {
        name: '待发条上的缩略图',
        note: '输入框里那张待发的图不吃这一行字（卡片上写着文件名）：方格里摆一个断图图标，位置先占住 —— 待发条不会因为一张图慢半拍而跳动。',
        props: { topicId: 't-1', path: 'uploads/2026-10-01/photo.png', thumb: true },
        expectSelector: '.im-thumb__failed',
      },
      {
        name: '话题和路径都还没有',
        note: '两个参数空着时不去问字节，什么也不画（外层那个方格还在占位）。',
        props: { topicId: null, path: '', thumb: true },
      },
    ],
  },
  {
    id: 'attachment-doc-thumb',
    title: 'AttachmentDocThumb',
    about:
      '一份文档的第一页，画在附件块那个方格里：PDF 直接取字节，Word 和幻灯片让平台先转一次；封面拼好之前，方格里摆的是这个文件类型自己的图标。',
    file: 'src/components/AttachmentDocThumb.vue',
    component: AttachmentDocThumb,
    needs: ['vuetify'],
    args: { topicId: 't-1', path: 'demo/评估汇报.pptx' },
    states: [
      {
        name: '封面还没画出来',
        note: '转换要约 2.5 秒，这期间方格不转圈，摆这个类型自己的图标 —— 一份幻灯片摆幻灯片的图标，不是一律 PDF：图标本身已经是一个正确的答案。',
        props: {},
        expectSelector: '.att-face .v-icon',
      },
    ],
  },
]
