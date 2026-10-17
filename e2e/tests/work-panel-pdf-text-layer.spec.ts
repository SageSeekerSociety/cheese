import { test, expect } from "@playwright/test";

// Browser geometry belongs here. pdf.js does not position the transparent spans
// itself: it writes the unscaled font height, the horizontal scale and the
// rotation as CSS variables and leaves the stylesheet to multiply them out by
// the scale it also wrote on the layer. happy-dom runs no layout, so a unit test
// can only see the variables, never whether the spans landed on the glyphs the
// canvas drew. Drop the multiplication and every span sits at the inherited font
// size with no scaleX: selecting one character grabs a whole span, and the amber
// stops short of the line. Mount the real PreviewPages over a page built here.
const fixture = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<div id="fixture"></div>
<script type="module">
import { createApp, h } from '/node_modules/.vite/deps/vue.js';
import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';
import PreviewPages from '/src/components/panels/preview/PreviewPages.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n, { setLocale } from '/src/i18n/index.ts';
import '/src/style.css';
import '/src/styles/fonts.css';

setLocale('zh-CN');

// One page: four plain lines, one line stretched with Tz, one line rotated. The
// stretched and rotated lines are the ones pdf.js hands a scaleX / a rotate to.
function page() {
  const content = [
    'BT /F1 18 Tf 5 0 0 5 5 5 Tm 0.9 0.9 0.9 rg 0 0 100 120 re f ET',
    '0.1 0.1 0.12 rg',
    'BT /F1 24 Tf 56 700 Td (The quick brown fox jumps over the lazy dog) Tj ET',
    'BT /F1 24 Tf 56 660 Td (pack my box with five dozen liquor jugs) Tj ET',
    'BT /F1 24 Tf 56 620 Td (how vexingly quick daft zebras jump) Tj ET',
    'BT /F1 24 Tf 56 580 Td (sphinx of black quartz judge my vow) Tj ET',
    'BT /F1 24 Tf 0 1 -1 0 520 200 Tm (Rotated line) Tj ET',
    'BT /F1 24 Tf 160 Tz 1 0 0 1 56 140 Tm (Stretched line) Tj ET',
  ].join('\\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    '<< /Length ' + content.length + ' >>\\nstream\\n' + content + '\\nendstream',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let pdf = '%PDF-1.4\\n';
  const offsets = [];
  objects.forEach((body, index) => {
    offsets.push(pdf.length);
    pdf += index + 1 + ' 0 obj\\n' + body + '\\nendobj\\n';
  });
  const xref = pdf.length;
  pdf += 'xref\\n0 ' + (objects.length + 1) + '\\n0000000000 65535 f \\n';
  pdf += offsets.map((o) => String(o).padStart(10, '0') + ' 00000 n \\n').join('');
  pdf += 'trailer\\n<< /Size ' + (objects.length + 1) + ' /Root 1 0 R >>\\nstartxref\\n' + xref + '\\n%%EOF';
  return new TextEncoder().encode(pdf);
}

const bytes = page();
createApp({ render: () => h(VApp, {}, { default: () => h('main', {
  style: 'width: 420px; height: 680px; display: flex; flex-direction: column',
}, [h(PreviewPages, { data: bytes.buffer })]) }) }).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`;

test("the pdf text layer takes pdf.js's geometry, and never becomes a scroll box", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/__pdf-text-layer-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: fixture }),
  );
  await page.goto("/__pdf-text-layer-fixture__", { waitUntil: "commit" });
  await expect(page.locator(".pv__page .pv-text span").first()).toBeVisible({
    timeout: 30_000,
  });

  const measured = await page.evaluate(() => {
    const layer = document.querySelector<HTMLElement>(".pv__page .pv-text")!;
    const spans = [...layer.querySelectorAll<HTMLElement>("span")].filter((s) =>
      s.textContent!.trim(),
    );
    const matrix = (span: HTMLElement) => {
      const parts = getComputedStyle(span).transform.match(/-?[\d.]+/g);
      return parts?.map(Number) ?? null;
    };
    // A text layer that is a scroll container scrolls itself away under a long
    // drag, which the reader sees as the amber highlight disappearing.
    layer.scrollTop = 40;
    layer.scrollLeft = 40;
    const scrolled = layer.scrollTop + layer.scrollLeft;
    layer.scrollTop = 0;
    layer.scrollLeft = 0;
    return {
      scale: Number.parseFloat(layer.style.getPropertyValue("--total-scale-factor")),
      overflow: getComputedStyle(layer).overflow,
      scrolled,
      spans: spans.map((span) => ({
        text: span.textContent!.slice(0, 12),
        fontHeight: Number.parseFloat(span.style.getPropertyValue("--font-height")),
        scaleX: Number.parseFloat(span.style.getPropertyValue("--scale-x")),
        fontSize: Number.parseFloat(getComputedStyle(span).fontSize),
        transform: getComputedStyle(span).transform,
        matrix: matrix(span),
      })),
    };
  });
  await test.info().attach("pdf-text-layer", {
    body: JSON.stringify(measured, null, 2),
    contentType: "application/json",
  });
  await page.evaluate(() => {
    // Leave the first line selected so the screenshot shows what the reader sees.
    const spans = [
      ...document.querySelectorAll<HTMLElement>(".pv__page .pv-text span"),
    ].filter((s) => s.textContent!.trim());
    const range = document.createRange();
    range.setStart(spans[0].firstChild!, 0);
    range.setEnd(spans[0].firstChild!, spans[0].textContent!.length);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await test.info().attach("pdf-text-layer-selection", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

  expect(measured.scale).toBeGreaterThan(0);
  expect(measured.spans.length).toBeGreaterThan(1);
  expect(measured.scrolled, "the text layer must not be scrollable").toBe(0);

  for (const span of measured.spans) {
    // The whole recipe: the unscaled font height times the scale pdf.js wrote.
    // An inherited font size means none of the variables reached the spans.
    expect(
      Math.abs(span.fontSize - span.fontHeight * measured.scale),
      `span ${span.text} is ${span.fontSize}px, not --font-height ${span.fontHeight} × ${measured.scale}`,
    ).toBeLessThan(0.1);
    expect(
      span.transform,
      `span ${span.text} must carry pdf.js's rotate/scaleX transform`,
    ).not.toBe("none");
  }

  // A stretched line keeps its width: this is what a long selection drifting off
  // the end of the line looks like before it is multiplied out.
  const widest = measured.spans.reduce((a, b) => (a.scaleX > b.scaleX ? a : b));
  expect(widest.scaleX).toBeGreaterThan(1.2);
  expect(widest.matrix?.[0]).toBeCloseTo(widest.scaleX, 2);

  // A rotated line is rotated.
  const rotated = measured.spans.find((s) => Math.abs(s.matrix?.[1] ?? 0) > 0.5);
  expect(rotated, "the rotated line must be rotated").toBeTruthy();
});
