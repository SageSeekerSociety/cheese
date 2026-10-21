import type { Locator } from '@playwright/test';

type Defect = { kind: string; what: string };

/** 一页表单把字段画成了哪一套 —— 量的人得先知道，因为「字段」和「标签」的盒子在两套
 *  里不是同一个选择器，而**判据**是同一个。
 *
 *  平台上只有两套：
 *
 *    * `VUETIFY_FIELDS` —— Vuetify 的 outlined 控件。真正画出来的 label 住在描边的
 *      缺口里（`.v-field__outline .v-field-label`），而字段是 `.v-field` 那个盒子。
 *    * `TOKEN_FIELDS` —— 平台自己用令牌画的表单（反馈提交页，见
 *      `components/feedback/SubmitFeedbackForm.vue` 的文件头：它**故意**不用 Vuetify
 *      的输入框）。那里一对「标签 + 控件」是 `.sb-field`，标签在控件上方一行，是普通
 *      的 `.sb-label`，没有骑在边框上的那一半。
 *
 *  两套要守的是同一件事：**标签不压到别的字段上、也不被滚动容器裁掉**。2026-09 换成
 *  令牌之后这条用例红过一次，红在「`.sb-form` 里一个 `.v-field` 都没有」——量不到字段
 *  时这个函数会当场炸（空范围永远返回「没有缺陷」），而那不是缺陷、是**画法变了**。
 *  把画法变成参数，判据一个字不用改。 */
type FieldDrawing = { field: string; label: string };
const VUETIFY_FIELDS: FieldDrawing = { field: '.v-field', label: '.v-field__outline .v-field-label' };
export const TOKEN_FIELDS: FieldDrawing = { field: '.sb-field', label: '.sb-label' };

export async function fieldDefects(scope: Locator, drawing: FieldDrawing = VUETIFY_FIELDS): Promise<Defect[]> {
  return scope.evaluate((root: Element, { field, label: labelSelector }: FieldDrawing) => {
    const out: { kind: string; what: string }[] = [];
    const drawn = (el: Element) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    const boxes = [...root.querySelectorAll(field)].filter(drawn);
    // 量不到字段就是范围选错了。空范围永远返回「没有缺陷」，是一条只会绿的断言，
    // 所以这里炸掉而不是放过：`.v-overlay__content` 的第一个是 tooltip 的浮层而
    // 不是对话框，这一条就是这么发现的。
    if (!boxes.length) throw new Error('这个范围里一个字段都没有，这条断言等于没做');

    const nameOf = (el: Element) =>
      (el.querySelector('.v-field-label, .sb-label')?.textContent || '').trim() || '(无标签字段)';
    const scrollerOf = (el: Element) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const overflow = getComputedStyle(p).overflowY;
        if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return p;
      }
      return null;
    };

    // outlined 变体真正画出来的那个 label 住在描边的缺口里；字段内部还有一个同名
    // 副本，是 visibility:hidden 的占位，量它只会得到错的坐标。（令牌画的那一套里
    // 标签本来就是元素的文字，没有这种副本，这个过滤对它无害。）
    const labels = [...root.querySelectorAll(labelSelector)].filter(
      (el) => getComputedStyle(el).visibility !== 'hidden' && drawn(el)
    );

    for (const label of labels) {
      const own = label.closest(field);
      const text = (label.textContent || '').trim();
      const lr = label.getBoundingClientRect();

      for (const box of boxes) {
        if (box === own) continue;
        const br = box.getBoundingClientRect();
        // 1px 容差：边框相接不算压上，真叠进去才算。
        const hit = lr.left < br.right && lr.right > br.left && lr.top < br.bottom - 1 && lr.bottom > br.top + 1;
        if (hit) out.push({ kind: 'overlap', what: `「${text}」压在「${nameOf(box)}」上` });
      }

      const scroller = scrollerOf(label);
      const fr = own?.getBoundingClientRect();
      if (scroller && fr) {
        const sr = scroller.getBoundingClientRect();
        // 只看「字段本身完整露着、label 却被切掉」。字段滚到视野外是正常的滚动，
        // 不是缺陷。
        const fieldFullyInView = fr.top >= sr.top - 0.5 && fr.bottom <= sr.bottom + 0.5;
        if (fieldFullyInView && (lr.top < sr.top - 0.5 || lr.bottom > sr.bottom + 0.5))
          out.push({ kind: 'clipped', what: `「${text}」被它所在的滚动容器裁掉` });
      }
    }
    return out;
  }, drawing);
}
