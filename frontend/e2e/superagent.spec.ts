import {expect, test} from "@playwright/test";

test("runs the SUP-001 scenario and opens an auditable result", async ({page}) => {
  await page.goto("/");

  await expect(page.getByRole("heading", {name: "Security SuperAgent"})).toBeVisible();
  const row = page.getByRole("row", {name: /Cross-domain lateral movement attack chain/});
  await expect(row).toBeVisible();
  await row.getByRole("button", {name: "智能研判"}).click();

  const drawer = page.locator(".ant-drawer-content");
  await expect(drawer.getByText("研判结果")).toBeVisible();
  await expect(drawer.locator(".ant-alert-message").getByText("真实威胁")).toBeVisible();
  await expect(drawer.getByText("Specialist Agent reports")).toBeVisible();
});
