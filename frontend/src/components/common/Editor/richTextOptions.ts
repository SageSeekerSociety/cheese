// 富文本工具栏里那几张选项表。取值与原先那台编辑器（vuetify-pro-tiptap）一致：老内容里
// 存的就是这些值，新设的格式与老内容同一套写法，两边混在一段正文里也一样显示、一样存。

/**
 * 文字颜色与高亮颜色的色板。这些是写进正文的内容颜色（作者挑的颜色，存在数据里），
 * 不是界面颜色，所以不走主题变量：深浅两套主题下都按原色显示。
 */
export const CONTENT_COLORS = [
  '#f44336',
  '#e91e63',
  '#9c27b0',
  '#673ab7',
  '#3f51b5',
  '#2196f3',
  '#03a9f4',
  '#00bcd4',
  '#009688',
  '#4caf50',
  '#8bc34a',
  '#cddc39',
  '#ffeb3b',
  '#ffc107',
  '#ff9800',
  '#ff5722',
  '#000000',
  '#333333',
  '#666666',
  '#999999',
  '#cccccc',
  '#d5d5d4',
  '#e8e8e8',
  '#eeeeee',
]

/** 字体：字体名本身就是选项的名字。 */
export const FONT_FAMILIES = [
  'Arial',
  'Arial Black',
  'Georgia',
  'Impact',
  'Helvetica',
  'Tahoma',
  'Times New Roman',
  'Verdana',
  'Courier New',
  'Monaco',
  'monospace',
]

/** 字号（px）。存成不带单位的字符串，与老内容一样。 */
export const FONT_SIZES = [8, 10, 12, 14, 16, 18, 20, 24, 30, 36, 48, 60, 72]

export const HEADING_LEVELS = [1, 2, 3, 4, 5, 6] as const

export const ALIGNMENTS = [
  { value: 'left', key: 'alignLeft', icon: 'mdi-format-align-left' },
  { value: 'center', key: 'alignCenter', icon: 'mdi-format-align-center' },
  { value: 'right', key: 'alignRight', icon: 'mdi-format-align-right' },
  { value: 'justify', key: 'alignJustify', icon: 'mdi-format-align-justify' },
] as const
