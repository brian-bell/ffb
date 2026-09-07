import { expect, test } from "@playwright/test";

for (const path of ["/", "/mock"]) {
  test(`${path} plays the jingle on demand and restarts without overlapping`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
    await page.route("**/api/draft", route => route.fulfill({ json: { configured: false, picks: [] } }));
    await page.goto(path);
    const button = page.getByRole("button", { name: "Play ESPN draft jingle" });
    await expect(button).toBeVisible();
    expect(await page.locator("audio").evaluateAll(elements => elements.every(el => (el as HTMLAudioElement).paused))).toBe(true);
    await button.click();
    const audio = page.locator("audio[data-draft-jingle-audio]");
    await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.currentTime)).toBeGreaterThan(0.5);
    await button.click();
    expect(await audio.evaluate((el: HTMLAudioElement) => el.currentTime)).toBeLessThan(0.5);
    await expect(audio).toHaveCount(1);
    await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.ended), { timeout: 15000 }).toBe(true);
    await button.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.paused)).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("playback failure is visible and the button can retry", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    localStorage.setItem("ffb.trackerKey", "test-secret-key");
    HTMLMediaElement.prototype.play = () => Promise.reject(new Error("Playback blocked"));
  });
  await page.goto("/mock");
  const button = page.getByRole("button", { name: "Play ESPN draft jingle" });
  await button.click();
  await expect(page.locator("[data-jingle-status]")).toHaveText("Couldn’t play the jingle. Try again.");
  await expect(button).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
