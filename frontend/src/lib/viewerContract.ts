// 预览查看器契约：每个查看器只回答三件事，别的都交给宿主。
//
// 现在八类内容各写一套——每种查看器自己提取选区、自己判定能不能发、自己核版本——加一类
// 能力要改好几处，于是能力永远不齐。这里把「查看器该回答什么」写成一个类型，宿主
// （`usePreviewLocator` 与预览格）照它统一做版本重核、结构化引用、@ 队友、发送和撤销。
//
// 这一层只声明形状，不取数、不画界面。接一个新类型只要实现这三问，不再各写一条发送路径。
import type { DocumentIdentity } from './documentIdentity'
import type { QuotedContext } from './quotedContext'

/** 一种指法：这一类内容允许读者怎么指。 */
export type Pointer =
  /** 选中一段文字。 */
  | 'text-range'
  /** 点文档里的一处（页码 + 页内比例）。 */
  | 'page-pin'
  /** 表格里的一格。 */
  | 'sheet-cell'
  /** 表格里的一片。 */
  | 'sheet-range'
  /** 幻灯片的一页，或页里选中的一段。 */
  | 'slide-page'
  /** 网页里点中的元素 / 选中的一段。 */
  | 'web-element'
  | 'web-text'
  /** 圈一块区域（没有注入桥的网页）。 */
  | 'web-region'

/** 查看器回答的第一问：这是哪一版。
 *
 *  带文件身份的那些指法要它才能指回同一份；`web-region` 例外——一个应用没有「那一版
 *  文件」可指，地址就是它唯一的身份（见 `quotedContext.ts` 的说明）。 */
export type ViewerVersion = DocumentIdentity

/** 查看器回答的第二问：能指哪些位置。
 *
 *  一类内容的指法是固定的（表格能点格、拖片；markdown 只能选段），所以这是一份声明
 *  而不是一个函数：宿主照它决定这一格摆哪几种指认的手势。 */
export interface ViewerCapability {
  /** 这一类的指法。空表是「只能看，指不了」（例如 Office 网页视图现在这一档）。 */
  pointers: readonly Pointer[]
  /** 指认要不要在一份已核过的字节上进行（幻灯片、分页文档是，表格与 markdown 不是）：
   *  为真时宿主发出去之前会拿这份身份再核一次，不成立就不发。 */
  verifiedBytes?: boolean
}

/** 查看器回答的第三问：当前指的是哪一处。
 *
 *  `quote` 是按 `quotedContext.ts` 拼好的那一处（宿主已经配上了文件身份），拿不到身份
 *  时是 null——那一格会退回拼一句话那条老路，不硬造引用。 */
export interface ViewerPick {
  /** 指着的这一处，作为一条结构化引用。 */
  quote: QuotedContext | null
  /** 定位条上写的那个位置（「第 2 页」「B7」「Sheet1!A1:C3」），给读者看的。 */
  label: string
  /** 跟着这一处的一句话：读者写的批注。 */
  note: string
}

/** 契约本身：查看器只回答这三问，宿主据此完成发送。 */
export interface ViewerContract {
  /** 这是哪一版。 */
  version: () => ViewerVersion | null
  /** 能指哪些位置。 */
  capability: () => ViewerCapability
  /** 当前指的是哪一处。 */
  pick: () => ViewerPick | null
}
