// ---- 这个房间里摆出来的东西 (#1085 结论四) ----

// 摆出来的东西属于这个房间：用户看完拿走就完了。要把它留下来以后还用，由人按
// 「保存到资料库」——留着要用的东西是资料；交出去的东西走交付，那才上产物清单。
export interface RoomOutput {
  path: string
  mime: string
  kind: 'file' | 'app'
  shown_at: string
}

/** 平台的一份标准模板：从它新建的是一份带样式和【占位】的 Office 文件。 */
export interface DocumentTemplate {
  id: string
  name: string
  suffix: 'docx' | 'pptx' | 'xlsx'
  about: string
}
