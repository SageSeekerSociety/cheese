// 富文本里的图：正文只存附件 id，地址在画的时候才去换（见 composables/useAttachmentImages）。
//
// 节点名、属性名和 HTML 形状是已经存下来的数据的一部分 —— 题目、知识库存的是这个节点的
// JSON，公告存的是 `<attachment-img attachmentId=…>` 这段 HTML —— 改任何一个，老内容里的
// 图就读不回来了。图下面那行字是节点自己的文字内容（`text*`），不是属性。
import { mergeAttributes, Node } from '@tiptap/core'
import { VueNodeViewRenderer } from '@tiptap/vue-3'

import AttachmentImageView from './AttachmentImageView.vue'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    attachmentImage: {
      /** 在光标处插一张已经传成附件的图。 */
      setImage: (options: {
        attachmentId: number
        alt?: string | null
        title?: string | null
        width?: number | null
        height?: number | null
      }) => ReturnType
    }
  }
}

export const AttachmentImage = Node.create({
  name: 'attachmentImage',
  priority: 500,
  inline: false,
  group: 'block',
  content: 'text*',
  draggable: true,
  defining: true,

  addAttributes() {
    return {
      attachmentId: { default: null },
      alt: { default: null },
      title: { default: null },
      width: { default: null },
      height: { default: null },
    }
  },

  addNodeView() {
    return VueNodeViewRenderer(AttachmentImageView)
  },

  parseHTML() {
    return [
      {
        tag: 'attachment-img',
        getAttrs: (node) => {
          const attachmentId = node.getAttribute('attachmentId')
          return {
            attachmentId: attachmentId ? Number(attachmentId) : null,
            alt: node.getAttribute('alt'),
            title: node.getAttribute('title'),
            width: node.getAttribute('width'),
            height: node.getAttribute('height'),
          }
        },
      },
    ]
  },

  renderHTML({ HTMLAttributes }) {
    return ['attachment-img', mergeAttributes(HTMLAttributes), 0]
  },

  addCommands() {
    return {
      setImage:
        (options) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: options }),
    }
  },
})
