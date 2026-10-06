// A video: a Bilibili link on a line of its own, shown as the site's player.
//
// The Markdown is the link and nothing else, so the text still reads (and still
// opens the video) anywhere the player is not drawn. Only a whole line is a
// video: a link inside a sentence stays a link. The player's address is built
// from the video's id alone, never from the text the writer gave.
//
// Part of the document schema module (see ./index.ts): nothing here touches the
// DOM or imports the app.

import { mergeAttributes, Node, nodePasteRule } from '@tiptap/core'

const VIDEO_URL = String.raw`https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})[^\s]*`
const VIDEO_LINE = new RegExp(String.raw`^ {0,3}(${VIDEO_URL})[ \t]*(?:\n|$)`)
const VIDEO_START = new RegExp(String.raw`^ {0,3}https?://(?:www\.|m\.)?bilibili\.com/video/BV`, 'm')
/** Pasted text that is a video link and nothing else. */
const VIDEO_PASTE = new RegExp(String.raw`^${VIDEO_URL}$`, 'g')

/** The player for a Bilibili link, or null when the link is not one. */
export function videoEmbed(src: string): string | null {
  const match = new RegExp(`^${VIDEO_URL}$`).exec(src.trim())
  if (!match) return null
  const page = /[?&]p=(\d+)/.exec(src)?.[1]
  return `https://player.bilibili.com/player.html?bvid=${match[1]}${page ? `&p=${page}` : ''}&autoplay=0`
}

export const Video = Node.create({
  name: 'video',
  group: 'block',
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      src: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-video') ?? '',
        renderHTML: (attributes) => ({ 'data-video': attributes.src }),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-video]' }],
  renderHTML({ node, HTMLAttributes }) {
    const src = node.attrs.src as string
    const embed = videoEmbed(src)
    if (!embed) {
      return ['div', mergeAttributes(HTMLAttributes), ['a', { href: src, target: '_blank', rel: 'noopener' }, src]]
    }
    return [
      'div',
      mergeAttributes(HTMLAttributes),
      [
        'iframe',
        {
          src: embed,
          title: 'Bilibili',
          allowfullscreen: 'true',
          referrerpolicy: 'no-referrer',
          loading: 'lazy',
          frameborder: '0',
        },
      ],
    ]
  },
  renderText: ({ node }) => node.attrs.src as string,
  addPasteRules() {
    return [nodePasteRule({ find: VIDEO_PASTE, type: this.type, getAttributes: (match) => ({ src: match[0] }) })]
  },
  markdownTokenName: 'video',
  markdownTokenizer: {
    name: 'video',
    level: 'block',
    start: (src) => src.search(VIDEO_START),
    tokenize(src) {
      const match = VIDEO_LINE.exec(src)
      if (!match) return undefined
      return { type: 'video', raw: match[0], src: match[1] }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('video', { src: token.src }),
  renderMarkdown: (node) => (node.attrs?.src as string) ?? '',
})
