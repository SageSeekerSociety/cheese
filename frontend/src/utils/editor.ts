// editorjs-latex 的公式预览靠 KaTeX 的样式排版，它自己的 CSS 不带这份。
import 'katex/dist/katex.min.css'

import type { EditorConfig } from '@editorjs/editorjs'

import Codecup from '@calumk/editorjs-codecup'
import Checklist from '@editorjs/checklist'
import Delimiter from '@editorjs/delimiter'
import Header from '@editorjs/header'
import InlineCode from '@editorjs/inline-code'
import NestedList from '@editorjs/nested-list'
import Paragraph from '@editorjs/paragraph'
import Quote from '@editorjs/quote'
import Table from '@editorjs/table'
import TextVariantTune from '@editorjs/text-variant-tune'
import Underline from '@editorjs/underline'
import Warning from '@editorjs/warning'
import LaTeX from 'editorjs-latex'

import { t } from '@/i18n'

// A function, not a constant: the placeholders are read in the reader's language
// each time an editor is created.
export const defaultEditorConfig = (): EditorConfig =>
  ({
    placeholder: t('editor.question.placeholder'),
    tools: {
      paragraph: {
        class: Paragraph,
        inlineToolbar: true,
        tunes: ['textVariant'],
      },
      header: {
        class: Header,
        inlineToolbar: true,
        config: {
          placeholder: t('editor.question.headingPlaceholder'),
          levels: [2, 3, 4],
          defaultLevel: 3,
        },
      },
      quote: {
        class: Quote,
        inlineToolbar: true,
      },
      delimiter: Delimiter,
      warning: Warning,
      math: LaTeX,
      code: Codecup,
      table: Table,
      textVariant: {
        class: TextVariantTune,
        inlineToolbar: true,
      },
      underline: {
        class: Underline,
        inlineToolbar: true,
      },
      inlineCode: {
        class: InlineCode,
        inlineToolbar: true,
      },
      checklist: Checklist,
      nestedList: NestedList,
    },
  }) as EditorConfig
