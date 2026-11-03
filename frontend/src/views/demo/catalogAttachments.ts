/**
 * 附件这几件在预览站里的条目：一条消息里的那张图、一份文档的第一页，和把图放大看的
 * 那一层。
 *
 * 头两件画的是「取回来的字节」——图要先 fetch 再转 object URL（`<img src>` 带不了
 * 组件里，而是从外壳注入（`lib/attachmentSource.ts`），所以这两件自己只吃 props。
 * 预览站里没有外壳，注入的是那份「取不到」的空实现 —— 于是这里摆的正是产品里真会走到
 * 的那几格：图取不回来时正文里那一句、待发条上那个断图图标、文档方格里那个类型图标，
 * 没有一格是空白。
 *
 * 放大看的那一层（`ImageLightbox`）反过来，一个字节都不取：地址是调用方交过来的
 * （就是上面那张图转出来的那份 object URL），所以这里直接喂两张现成的图，摆它对窗口
 * 里两种尺寸的处理。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和
 * `catalogViews.ts`、`catalogFeedback.ts` 同一个理由。这里的 `CatalogEntry` 是 type-only
 * 引用：`catalog.ts` 反过来要 `ATTACHMENT_ENTRIES` 这个值，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import AttachmentDocThumb from '@/components/AttachmentDocThumb.vue'
import AttachmentImage from '@/components/AttachmentImage.vue'
import AttachmentVideo from '@/components/AttachmentVideo.vue'
import ImageLightbox from '@/components/ImageLightbox.vue'

/** 预览站里的两张图：内联 SVG，不去网上取，也不进仓库（`src` 吃的是一个地址，什么地址都行）。
 *  给宽高是为了让浏览器报得出原尺寸 —— 这一层按原尺寸和窗口谁小来算摆多大。 */
const LANDSCAPE =
  'data:image/svg+xml;charset=utf-8,<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900">' +
  '<rect width="1600" height="900" fill="%23cfe0f5"/><circle cx="1180" cy="270" r="120" fill="%23f2b464"/>' +
  '<path d="M0 900 L520 470 L900 900 Z" fill="%236f8fb8"/><path d="M620 900 L1180 540 L1600 900 Z" fill="%235a7799"/></svg>'
const PORTRAIT =
  'data:image/svg+xml;charset=utf-8,<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1600">' +
  '<rect width="900" height="1600" fill="%23f4efe4"/><rect x="90" y="120" width="720" height="1360" rx="24" fill="%23e0d6c2"/>' +
  '<circle cx="450" cy="520" r="150" fill="%23c9a227"/><rect x="240" y="820" width="420" height="36" rx="18" fill="%23867a63"/>' +
  '<rect x="240" y="900" width="300" height="36" rx="18" fill="%23867a63"/></svg>'

export const ATTACHMENT_ENTRIES: CatalogEntry[] = [
  {
    id: 'attachment-video',
    title: 'AttachmentVideo',
    about: '对话里的视频附件：点开后加载，手动播放；无法读取或解码时仍可下载。',
    file: 'src/components/AttachmentVideo.vue',
    component: AttachmentVideo,
    needs: ['vuetify', 'i18n'],
    args: { topicId: 't-1', path: 'library/screen.mp4' },
    states: [
      {
        name: '等待点开',
        note: '进入对话不会加载视频字节或自动播放。点开才尝试读取；目录站没有字节源，会显示读取失败与重新加载入口。',
        props: {},
        expectSelector: '.video-card',
      },
    ],
  },
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
  {
    id: 'image-lightbox',
    title: 'ImageLightbox',
    about:
      '消息里那张图放大看的那一层：铺满窗口、滚轮或双指缩放、按住拖动，点空白或按 Esc 收起 —— 不开新标签页（那份字节只活在这一页里）。',
    file: 'src/components/ImageLightbox.vue',
    component: ImageLightbox,
    needs: ['vuetify', 'i18n'],
    // 这一层画在 body 上（铺满整个窗口），不在挂它的那个容器里。
    teleport: true,
    args: { src: LANDSCAPE, alt: '海岸线.png' },
    states: [
      {
        name: '横向的一张图',
        note: '宽的一张按窗口宽度缩到整张都看得见，不是按原尺寸铺开：看第一眼要的是「这是哪张图」，细节靠滚轮放大。',
        props: {},
        expectSelector: '.lightbox__figure',
      },
      {
        name: '竖向的一张图',
        note: '高的一张按窗口高度缩，见到的还是完整一张 —— 裁掉上下两端就成了另一张图。',
        props: { src: PORTRAIT, alt: '海报.png' },
        expectSelector: '.lightbox__figure',
      },
    ],
  },
]
