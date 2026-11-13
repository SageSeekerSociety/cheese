import { expect, test } from "@playwright/test";

import { DEMO_PASSWORD, acceptPendingConsents } from "./helpers";

// A new account goes through the new-user guide: it is offered a starter task
// for the role it picked, the project is made only when the person clicks
// 领这道题, and the project's 综合 opens with the first request written and the
// guide beside it. grace, because no
// other spec signs in as her; the CI seed puts every demo account in a team
// that has projects, so she first leaves hers (全栈开发小队, team 2) and has
// none, like an account just registered.
test("a new account is guided into its first project", async ({ page }) => {
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
  const projects = async () =>
    (
      await (
        await page.request.get("/api/projects", {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
      ).json()
    ).data.data as { name: string }[];

  await page.goto("/");
  await expect(page).toHaveURL(/\/welcome/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "你主要用知是做什么？" })).toBeVisible();
  await page.getByRole("button", { name: /上课、做课程项目/ }).click();
  await page.getByRole("button", { name: "继续" }).click();
  // A student is offered the web-page starter first.
  await expect(page.getByRole("heading", { name: "先领一道新手题" })).toBeVisible();
  await expect(page.getByRole("button", { name: /做一个活动主页/ })).toHaveAttribute("aria-pressed", "true");
  // Nothing is made until the person says so.
  expect(await projects()).toEqual([]);
  await page.getByRole("button", { name: "领这道题" }).click();

  await expect(page).toHaveURL(/\/projects\/.+\/channels\//, { timeout: 30_000 });
  await expect(page.locator(".composer-input .ProseMirror").first()).toContainText(/三行计划/);
  // The guide carries on in the project, one step at a time, and can be skipped.
  await expect(page.getByText("1/8")).toBeVisible();
  await page.getByRole("button", { name: "跳过导览" }).click();
  await expect(page.getByText("1/8")).toBeHidden();
  expect((await projects()).map((p) => p.name)).toEqual(["新手题：做一个活动主页"]);

  // Coming back goes to the project, not to the guide again.
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\//, { timeout: 30_000 });
  expect((await projects()).length).toBe(1);
});
