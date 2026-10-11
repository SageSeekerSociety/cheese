// Records the new-user guide on this branch, step by step, for the walkthrough
// PDF and video. Copied into e2e/tests on mac-mini only, never committed.
import { test, type Page } from "@playwright/test";

const OUT = "../logs/walk";
test.use({ viewport: { width: 1280, height: 800 }, video: { mode: "on", size: { width: 1280, height: 800 } } });

async function shot(page: Page, name: string) {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test("new account walkthrough", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/account/signin");
  await page.getByLabel("用户名").waitFor();
  await page.getByLabel("用户名").fill("henry");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await shot(page, "01-signin");
  await page.getByRole("button", { name: "登录", exact: true }).click();

  // Demo accounts have the current terms pending; a real sign-up agreed to them already.
  const agree = page.getByRole("button", { name: "同意并继续" });
  await agree.waitFor({ timeout: 30_000 });
  await agree.click();

  await page.getByRole("heading", { name: "芝士怎么称呼你？" }).waitFor({ timeout: 60_000 });
  await shot(page, "02-name");
  const next = page.getByRole("button", { name: "继续" });
  await next.click();

  await page.getByRole("button", { name: /上课、做课程项目/ }).click();
  await page.getByLabel("学校或单位（选填）").fill("中国人民大学");
  await shot(page, "03-role");
  await next.click();

  await page.getByRole("button", { name: /写课程报告或论文初稿/ }).click();
  await shot(page, "04-task");
  await next.click();

  await shot(page, "05-who");
  await next.click();

  await shot(page, "06-where");
  await page.getByRole("button", { name: "建好项目" }).click();

  await page.waitForURL(/\/channels\//, { timeout: 60_000 });
  await page.getByText("交给芝士第一件事").waitFor({ timeout: 30_000 });
  console.log("landed", page.url());

  // The interface tour: one highlighted stop at a time.
  for (let i = 1; i <= 5; i += 1) {
    await page.getByText(`${i}/5`).waitFor({ timeout: 30_000 });
    await shot(page, `07-tour-${i}`);
    await page.getByRole("button", { name: i === 5 ? "完成" : "下一步" }).click();
  }
  await shot(page, "07-general");

  // The next visit, before 芝士 has said anything: back to 综合, steps still there.
  await page.goto("/");
  await page.waitForURL(/\/projects\//, { timeout: 30_000 });
  await page.waitForTimeout(3000);
  await shot(page, "08-next-visit");
  console.log("next visit", page.url());
});
