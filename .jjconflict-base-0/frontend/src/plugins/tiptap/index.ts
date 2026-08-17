import 'vuetify-pro-tiptap/style.css'

import type { InstallationOptions } from 'vuetify-pro-tiptap'

import { createVuetifyProTipTap, VuetifyTiptap, VuetifyViewer } from 'vuetify-pro-tiptap'
import {
  BaseKit,
  Blockquote,
  Bold,
  BulletList,
  Clear,
  Code,
  CodeBlock,
  Color,
  FontFamily,
  FontSize,
  Fullscreen,
  Heading,
  Highlight,
  History,
  HorizontalRule,
  Indent,
  Italic,
  Link,
  OrderedList,
  Strike,
  SubAndSuperScript,
  Table,
  TaskList,
  TextAlign,
  Underline,
} from 'vuetify-pro-tiptap'
import HardBreak from '@tiptap/extension-hard-break'

import { AttachmentImage } from './extensions/image'

// The mirror image of the bridge in TipTapViewer.vue: this list is
// vuetify-pro-tiptap's (tiptap v2), and HardBreak / AttachmentImage are the two
// entries built against the app's tiptap v3. Same duplicate-package cause, same
// no-op at runtime; `asVptExtension` marks exactly which entries straddle the
// boundary instead of casting the whole array and blinding the other 25.
type VptExtension = NonNullable<InstallationOptions['extensions']>[number]
const asVptExtension = (extension: unknown) => extension as VptExtension

export const vuetifyProTipTap = createVuetifyProTipTap({
  lang: 'zhHans',
  components: {
    VuetifyTiptap,
    VuetifyViewer,
  },
  extensions: [
    BaseKit.configure({
      placeholder: {
        placeholder: '输入内容...',
      },
    }),
    asVptExtension(HardBreak),
    Bold,
    Italic,
    Underline,
    Strike,
    Code.configure({ divider: true }),
    Heading,
    TextAlign,
    FontFamily,
    FontSize,
    Color,
    Highlight.configure({ divider: true }),
    SubAndSuperScript.configure({ divider: true }),
    Clear.configure({ divider: true }),
    BulletList,
    OrderedList,
    TaskList,
    Indent.configure({ divider: true }),
    Link,
    asVptExtension(AttachmentImage),
    Table.configure({ divider: true }),
    Blockquote,
    HorizontalRule,
    CodeBlock.configure({ divider: true }),
    History.configure({ divider: true }),
    Fullscreen,
  ],
})
