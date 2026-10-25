// Own, ephemeral browser profile; connects only to the local synthetic server.
const { chromium } = require(process.argv[3] || "playwright");
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.argv[4]});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(process.argv[2], {waitUntil: "domcontentloaded"});
    await page.waitForFunction(() => document.getElementById("monitorHealth").textContent.includes("unavailable"));
    const health = await page.locator("#monitorHealth").textContent();
    const activity = await page.evaluate(() => {
      renderIncidentActivity({ts: 300, timeline: []});
      const empty = Array.from(document.querySelectorAll(".heatmap-cell")).map(cell => cell.style.backgroundColor);
      renderIncidentActivity({ts: 300, timeline: [{start: 290}]});
      const filled = Array.from(document.querySelectorAll(".heatmap-cell")).map(cell => cell.style.backgroundColor);
      renderIncidentActivity({ts: 300, timeline: []});
      return empty.filter((value, i) => value !== filled[i]).length;
    });
    const result = await page.evaluate(async () => {
      const response = await apiFetch("/api/source", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source: "0"})
      });
      return response.status;
    });
    if (result !== 200 || errors.length || activity !== 1) throw new Error(JSON.stringify({result, errors, activity}));
    await page.screenshot({path: process.argv[5], fullPage: true});
    console.log(JSON.stringify({browser: "passed", authenticatedMutation: result, activityBinsVerified: activity, health, errors}));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
