import { expect, test } from "@playwright/test";

import { DEMO_PASSWORD, acceptPendingConsents } from "./helpers";

// A new account's first screen is its own project's 综合, with the starter
// cards: no "new project" to find first, and nothing is sent until the person
// sends it. grace, because no other spec signs in as her; the CI seed puts
// every demo account in a team that has projects, so she first leaves hers
// (全栈开发小队, team 2) and has none, like an account just registered.
test("a new account lands in its own project and starts from a card", async ({ page }) => {
  const signedIn = await page.request.post("/api/users/auth/login", {
    data: { username: "grace", password: DEMO_PASSWORD },
  });
  expect(signedIn.ok()).toBe(true);
  const { accessToken, user } = (await signedIn.json()).data;
  await acceptPendingConsents(page, accessToken);
  // A retry finds her already out of it.
  const left = await page.request.delete("/api/users/me/teams/2", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  expect([200, 204, 404]).toContain(left.status());
  await page.goto("/favicon.ico");
  await page.evaluate(
    ({ accessToken, user }) => {
      localStorage.setItem("accessToken", accessToken);
      localStorage.setItem("user", JSON.stringify(user));
      localStorage.setItem("cheese:locale", "zh-CN");
    },
    { accessToken, user },
  );

  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\//, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "你想先做点什么？" })).toBeVisible();
  await page.getByRole("button", { name: /查资料，写成一份报告/ }).click();
  const box = page.getByRole("textbox").last();
  await expect(box).toHaveValue(/注明来源/);
  await expect(page.getByRole("heading", { name: "你想先做点什么？" })).toBeHidden();

  // Coming back makes no second project.
  const projects = async () =>
    (
      await (
        await page.request.get("/api/projects", { headers: { Authorization: `Bearer ${accessToken}` } })
      ).json()
    ).data.data as { name: string }[];
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\//, { timeout: 30_000 });
  expect((await projects()).map((p) => p.name)).toEqual(["Grace的项目"]);
});
