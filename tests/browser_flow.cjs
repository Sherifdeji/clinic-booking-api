/* Optional real-Chrome checks. Requires playwright outside application dependencies.
 * Run only against the seeded disposable audit database described in docs/AUDIT.md.
 */
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const errors = [];
  const context = await browser.newContext({ timezoneId: "Africa/Lagos", reducedMotion: "reduce" });
  context.on("page", (page) => {
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      assert.equal(request.headers()["x-api-key"], undefined, "browser must never receive or send the private key");
    });
  });
  const page = await context.newPage();
  const base = process.env.BASE_URL || "http://127.0.0.1:58123";
  const screenshots = process.env.SCREENSHOT_DIR || path.resolve("docs/screenshots");
  let checks = 0;
  async function checkLayout(width, height, name) {
    await page.setViewportSize({ width, height });
    await page.goto(base);
    await page.waitForSelector(".slot-card");
    assert.equal(await page.locator("#slots-loading").isVisible(), false);
    assert.equal(await page.locator("#slots-empty").isVisible(), false);
    assert.equal(await page.locator("#confirmation").isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator("#patient-name").evaluate((input) => input.form.id), "booking-form");
    assert.equal(await page.locator(".slot-card.unavailable button:enabled").count(), 0);
    await page.screenshot({ path: path.join(screenshots, `after-${name}.png`), fullPage: true });
    checks += 6;
  }
  try {
    await checkLayout(1440, 1000, "desktop");
    await checkLayout(390, 844, "mobile");
    await checkLayout(320, 760, "small-mobile");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.selectOption("#provider-filter", "Dr Ade");
    assert.equal(await page.locator(".slot-provider").allTextContents().then((names) => names.every((name) => name === "Dr Ade")), true);
    await page.selectOption("#provider-filter", "");
    await page.locator('.select-button[data-bookable="true"]').first().click();
    await page.locator("#submit-booking").click();
    assert.equal(await page.locator("#patient-name").getAttribute("aria-invalid"), "true");
    assert.equal(await page.locator("#patient-name").evaluate((input) => input === document.activeElement), true);
    await page.fill("#patient-name", "Browser Patient");
    await page.fill("#patient-email", "browser-patient@example.com");
    let posts = 0;
    let responseBody;
    await page.route("**/ui-api/bookings", async (route) => {
      posts++;
      await new Promise((resolve) => setTimeout(resolve, 250));
      const response = await route.fetch();
      responseBody = await response.json();
      await route.fulfill({ response });
    });
    await page.locator("#booking-form").evaluate((form) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await page.waitForFunction(() => document.querySelector("#patient-name").disabled);
    assert.equal(await page.locator("#provider-filter").isDisabled(), true);
    assert.equal(await page.locator(".select-button:enabled").count(), 0);
    await page.waitForSelector("#confirmation", { state: "visible" });
    assert.equal(posts, 1);
    assert.equal(await page.locator("#confirmation-id").textContent(), responseBody.id);
    assert.equal(await page.locator("#confirmation-name").textContent(), responseBody.name);
    assert.equal(await page.locator("#confirmation-status").textContent(), responseBody.status);
    assert.equal(await page.locator("#appointments").isVisible(), false);
    await page.screenshot({ path: path.join(screenshots, "confirmation-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(screenshots, "confirmation-mobile.png"), fullPage: true });
    checks += 10;

    // Lose the first response after a real committed booking, then safely replay it.
    await page.unroute("**/ui-api/bookings");
    await page.locator("#book-another").click();
    await page.waitForSelector('.select-button[data-bookable="true"]:enabled');
    await page.locator('.select-button[data-bookable="true"]').first().click();
    await page.fill("#patient-name", "Retry Patient");
    await page.fill("#patient-email", "retry-patient@example.com");
    const keys = [];
    let original;
    await page.route("**/ui-api/bookings", async (route) => {
      keys.push(route.request().headers()["idempotency-key"]);
      const response = await route.fetch();
      if (keys.length === 1) {
        original = await response.json();
        await route.abort("failed");
      } else {
        assert.deepEqual(await response.json(), original);
        await route.fulfill({ response });
      }
    });
    await page.locator("#submit-booking").click();
    await page.waitForSelector("#global-message", { state: "visible" });
    await page.waitForFunction(() => !document.querySelector("#submit-booking").disabled);
    const storage = await page.evaluate(() => sessionStorage.getItem("koya.booking-retries.v1"));
    assert.equal(storage.includes("retry-patient"), false);
    assert.equal(await page.locator("#confirmation").isVisible(), false);
    await page.locator("#submit-booking").click();
    await page.waitForSelector("#confirmation", { state: "visible" });
    assert.equal(keys.length, 2);
    assert.equal(keys[0], keys[1]);
    assert.equal(await page.locator("#confirmation-id").textContent(), original.id);
    checks += 6;
    assert.deepEqual(errors, []);
    console.log(`PASS: ${checks} browser assertions; desktop, mobile, 320px, actual booking, double-submit guard, and lost-response replay.`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
