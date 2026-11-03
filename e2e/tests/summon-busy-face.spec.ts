import { expect, test, type Page } from "@playwright/test";

// The teammate's face beside Send. Not handed this message, it sleeps: eyes
// shut. Running a turn in this place, so it will read this message anyway, it
// looks up and to the right with its eyes open (#4446). How the eyes are drawn
// is the stylesheet's doing, and only a real browser applies it: the unit tests
// run with stylesheets stubbed out.
//
// Motion is reduced so the face holds still: CheeseAvatar then stops every
// animation, and what is measured is the drawing itself, not a frame of the
// blink.

type Face = { on: boolean; busy: boolean };

function fixture(face: Face): string {
  return (
    '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">' +
    "<title>summon face</title></head>\n<body>\n" +
    '<div id="host"></div>\n' +
    '<script type="module">\n' +
    "import { createApp, h } from '/node_modules/.vite/deps/vue.js';\n" +
    "import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';\n" +
    "import ComposerSummon from '/src/components/room/ComposerSummon.vue';\n" +
    "import vuetify from '/src/plugins/vuetify.ts';\n" +
    "import i18n, { setLocale } from '/src/i18n/index.ts';\n" +
    "import '/src/style.css';\n" +
    "setLocale('zh-CN');\n" +
    "const mate = { handle: 'cheese-s', label: '芝士' };\n" +
    "createApp({ render: () => h(VApp, {}, { default: () => h(ComposerSummon, {\n" +
    `  mates: [mate], shown: [mate], on: ${face.on}, fixed: false, ready: true,\n` +
    `  busy: ${face.busy ? "[mate.handle]" : "[]"},\n` +
    "}) }) }).use(vuetify).use(i18n).mount('#host');\n" +
    "</script></body></html>"
  );
}

/** The near eye's drawn height over its height in the drawing (1 = open). */
async function eyeOpenness(page: Page, face: Face): Promise<number> {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/__summon-face__", (route) =>
    route.fulfill({ contentType: "text/html", body: fixture(face) }),
  );
  await page.goto("/__summon-face__", { waitUntil: "commit" });
  const eye = page.locator(".summon__btn .cheese-avatar__eye").first();
  await expect(eye).toBeAttached({ timeout: 60_000 });
  // Let the eye's transition from the first frame finish.
  await page.waitForTimeout(600);
  return eye.evaluate((el) => {
    const svg = el as SVGGraphicsElement;
    const drawn = svg.getBoundingClientRect().height;
    const ctm = svg.ownerSVGElement!.getScreenCTM()!;
    return drawn / (svg.getBBox().height * ctm.d);
  });
}

test.describe("the teammate face beside Send", () => {
  test("not handed the message and idle: eyes shut", async ({ page }) => {
    expect(await eyeOpenness(page, { on: false, busy: false })).toBeLessThan(0.5);
  });

  test("not handed the message but running a turn here: eyes open", async ({ page }) => {
    expect(await eyeOpenness(page, { on: false, busy: true })).toBeGreaterThan(0.9);
  });

  test("handed the message: eyes open", async ({ page }) => {
    expect(await eyeOpenness(page, { on: true, busy: false })).toBeGreaterThan(0.9);
  });
});
