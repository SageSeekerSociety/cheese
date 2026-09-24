import { test, expect } from "@playwright/test";
import { DEMO_USERNAME, DEMO_PASSWORD } from "./helpers";

test.describe("Login", () => {
  test("valid credentials sign the user in and land on the authenticated app shell", async ({
    page,
  }) => {
    await page.goto("/account/signin");
    await page.getByLabel("用户名").fill(DEMO_USERNAME);
    await page.getByLabel("密码", { exact: true }).fill(DEMO_PASSWORD);
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await expect(page.getByText("登录成功")).toBeVisible();
    await page
      .locator(".app-rail-item:not(.app-rail-item--add)")
      .first()
      .waitFor();
    const accessToken = await page.evaluate(() =>
      localStorage.getItem("accessToken"),
    );
    expect(accessToken).toBeTruthy();
  });

  test("wrong credentials are rejected and the user stays on the sign-in page", async ({
    page,
  }) => {
    // A made-up username, not the shared demo account: after 5 failed
    // attempts the backend makes the next sign-in for that username wait (see
    // backend/app/domain/user/login_security.py LoginDelay), and that Redis
    // state outlives a single test run — on CI too: e2e.yml recreates the
    // Postgres database per run but never flushes the slot's Redis, and the
    // failures are remembered for an hour. Runs on one slot inside that window
    // (each up to 3 attempts with retries) would reach 5 and turn this test red
    // with the too-many-attempts message instead of the wrong-password one. So
    // the name is unique per run attempt, and failing against a throwaway name
    // also keeps this test from ever holding up `alice`, who the other specs
    // depend on.
    const noSuchUser = `no-such-user-e2e-${process.env.GITHUB_RUN_ID ?? "local"}-${process.env.GITHUB_RUN_ATTEMPT ?? "1"}`;
    await page.goto("/account/signin");
    await page.getByLabel("用户名").fill(noSuchUser);
    // exact: true — see helpers.ts::login for why (other labels on the page
    // contain 「密码」 and 「登录」 as substrings).
    await page.getByLabel("密码", { exact: true }).fill("wrong-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();

    // The page words the refusal itself, in the interface language (zh-CN
    // here): account.attempts.wrongPassword in the catalog.
    await expect(page.getByText("用户名或密码错误")).toBeVisible();
    await expect(page).toHaveURL(/\/account\/signin/);
    const accessToken = await page.evaluate(() =>
      localStorage.getItem("accessToken"),
    );
    expect(accessToken).toBeFalsy();
  });
});

test.describe("English login", () => {
  test.use({ locale: "en-US" });

  test("keeps the selected language after reload and signs in through the English form", async ({
    page,
  }) => {
    await page.goto("/account/signin");
    await expect(
      page.getByRole("heading", { name: "Sign in", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "切换到中文" }).click();
    await page.getByRole("button", { name: "Switch to English" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await page.getByLabel("Username").fill(DEMO_USERNAME);
    await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByText("Signed in", { exact: true })).toBeVisible();
    await page
      .locator(".app-rail-item:not(.app-rail-item--add)")
      .first()
      .waitFor();
    expect(
      await page.evaluate(() => localStorage.getItem("accessToken")),
    ).toBeTruthy();
  });
});
