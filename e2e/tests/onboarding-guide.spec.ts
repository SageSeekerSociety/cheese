import { expect, test } from "@playwright/test";

import { DEMO_PASSWORD, acceptPendingConsents } from "./helpers";

// A new account goes through the new-user guide: its answers become the first
// project, made only when the person clicks 建好项目, and the project's 综合
// opens with the first request written and 上手三步 beside it. grace, because no
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
  await expect(page.getByRole("heading", { name: "芝士怎么称呼你？" })).toBeVisible();
  const next = page.getByRole("button", { name: "继续" });
  await next.click();
  await page.getByRole("button", { name: /上课、做课程项目/ }).click();
  await next.click();
  await page.getByRole("button", { name: /写课程报告或论文初稿/ }).click();
  await next.click();
  await next.click();
  // Nothing is made until the person says so.
  expect(await projects()).toEqual([]);
  await page.getByRole("button", { name: "建好项目" }).click();

  await expect(page).toHaveURL(/\/projects\/.+\/channels\//, { timeout: 30_000 });
  await expect(page.getByRole("textbox").last()).toHaveValue(/先给我看提纲/);
  // The guide carries on in the project, one step at a time, and can be skipped.
  await expect(page.getByText("1/10")).toBeVisible();
  await page.getByRole("button", { name: "跳过导览" }).click();
  await expect(page.getByText("1/10")).toBeHidden();
  expect((await projects()).map((p) => p.name)).toEqual(["课程报告"]);

  // Coming back goes to the project, not to the guide again.
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\//, { timeout: 30_000 });
  expect((await projects()).length).toBe(1);
});
