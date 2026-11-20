// 「批注卡开着的时候，读者在说哪一段」这一份副本。
//
// 点开批注卡会把光标交给输入框，浏览器随即把页面上的选区收起来。底色是照选区画的，
// 选区一没，底色就跟着灭——读者正在写的那一段突然不亮了，看不出自己在说哪儿。
//
// 定位条那一层（`PanelPreviewView`）在打开时已经留了一份选区副本（`anchorRange`，算框
// 的位置也用它）；这里把它递到画底色的那一层（`PreviewPages`），卡开着的时候按副本画。
// 用 provide/inject 而不是逐层传 props：中间隔着 `PreviewDocStage`，而这一件只有头和尾
// 两处认得。
import type { InjectionKey, Ref } from 'vue'

export const KEPT_SELECTION: InjectionKey<Ref<Range | null>> = Symbol('kept-selection')
