"""Browser regression using real current data and route-only simulated history.

Run a Vite dev server first. This script never writes synthetic history to disk.
Install playwright and a Chromium browser; set GRID_BROWSER_CHANNEL=msedge on
Windows to use an existing Edge installation.
"""
import asyncio
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

from playwright.async_api import async_playwright

BASE = os.environ.get("GRID_TEST_URL", "http://127.0.0.1:5175/")


async def main():
    index = json.load(urlopen(BASE + "data/index.json"))
    current = json.load(urlopen(BASE + "data/" + index["snapshots"][0]["url"]))
    geometry = json.loads(gzip.decompress(urlopen(BASE + "data/" + current["geometry"]).read()))
    confirmed_id = next(key for key, match in current["matches"].items() if match["method"] == "operator_name_voltage")
    record_id = current["matches"][confirmed_id]["records"][0]
    prior = copy.deepcopy(current)
    prior["week"] = "2020-W01"  # Simulated only by request routing.
    prior["created_at"] = "2020-01-01T00:00:00+09:00"
    for utility in prior["utilities"].values():
        for record in utility["records"]:
            if record["id"] == record_id:
                record["available_mw"] = 123.5
                record["capacity_status"] = "AVAILABLE"
                utility["status"] = "stale"
                utility["error"] = "Simulated source failure"
    prior["stats"]["utilities_ok"] = 9
    prior["stats"]["utilities_stale"] = 1
    old_geometry = copy.deepcopy(geometry)
    old_geometry["lines"][0][-1][0][0] += 0.0001
    encoded = json.dumps(old_geometry).encode()
    prior["geometry"] = "geometry/" + hashlib.sha256(encoded).hexdigest() + ".json.gz"
    index["snapshots"].append({"week": prior["week"], "url": "snapshots/2020-W01.json", "created_at": prior["created_at"], "stats": prior["stats"]})
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=os.environ.get("GRID_BROWSER_CHANNEL") or None, headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Keep grid correctness independent of third-party basemap latency.
        await page.route("https://basemaps.cartocdn.com/gl/*/style.json", lambda route: route.fulfill(json={
            "version": 8, "sources": {}, "layers": [{"id": "background", "type": "background", "paint": {"background-color": "#16202a"}}]}))
        await page.route("**/data/index.json", lambda route: route.fulfill(json=index))
        await page.route("**/data/snapshots/2020-W01.json", lambda route: route.fulfill(json=prior))
        await page.route("**/data/" + prior["geometry"], lambda route: route.fulfill(body=gzip.compress(encoded), content_type="application/gzip"))
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.evaluate("async () => { window.grid = await import('/src/main.ts'); }")
        await page.wait_for_function("grid.map.getSource('grid') && grid.map.isSourceLoaded('grid')", timeout=60000)
        assert await page.locator("#snapshot-week").input_value() == current["week"]
        assert await page.evaluate("grid.map.getSource('grid')._data.features.length") == len(geometry["lines"])
        regions = [("Hokkaido", [142, 43]), ("Tohoku", [140.7, 38.4]), ("Tokyo", [139.4, 35.6]), ("Hokuriku", [136.8, 36.5]),
                             ("Chubu", [137, 35.5]), ("Kansai", [135.5, 34.8]), ("Chugoku", [133, 34.6]), ("Shikoku", [133.5, 33.9]),
                             ("Kyushu", [130.7, 32.7]), ("Okinawa", [127.8, 26.3])]
        print('Initial map ready', flush=True)
        for name, center in ([] if os.environ.get('GRID_SKIP_REGIONS') else regions):
            await page.evaluate("center => grid.map.jumpTo({center, zoom:8})", center)
            await page.wait_for_function("grid.map.isSourceLoaded('grid') && grid.map.queryRenderedFeatures({layers:['power-lines-core']}).length > 0", timeout=30000)
            print(name, "rendered", flush=True)
        await page.locator("#search").fill("__not_present__")
        await page.wait_for_function("grid.map.queryRenderedFeatures({layers:['power-lines-core']}).length === 0")
        await page.locator("#reset-filters").click()
        await page.wait_for_function("grid.map.queryRenderedFeatures({layers:['power-lines-core']}).length > 0")
        await page.locator("#snapshot-week").select_option(prior["week"])
        await page.wait_for_function("document.getElementById('dataset-status').textContent.startsWith('2020-W01 ·')")
        value = await page.evaluate("id => grid.map.getSource('grid')._data.features.find(f=>String(f.id)===id).properties.available_mw", confirmed_id)
        assert value == 123.5, {"value": value, "id": confirmed_id, "record": record_id, "status": await page.locator('#dataset-status').inner_text(), "errors": errors}
        assert await page.evaluate("grid.map.getSource('grid')._data.features[0].geometry.coordinates[0][0]") == old_geometry["lines"][0][-1][0][0]
        assert "sources need attention" in await page.locator("#dataset-status").inner_text()
        assert "week=2020-W01" in page.url
        await page.locator("#snapshot-week").select_option(current["week"])
        await page.wait_for_function("week => document.getElementById('dataset-status').textContent.startsWith(week+' ·')", arg=current["week"])
        assert await page.evaluate("grid.map.getSource('grid')._data.features[0].geometry.coordinates[0][0]") == geometry["lines"][0][-1][0][0]
        assert "?week=" not in page.url
        await page.locator("#basemap").select_option("gsi-pale")
        await page.wait_for_function("grid.map.getLayer('gsi-pale-layer') && grid.map.getSource('grid') && grid.map.isSourceLoaded('grid')", timeout=30000)
        await page.locator("#records-panel > summary").click()
        await page.locator("#utility").select_option("tepco")
        await page.locator("#record-search").fill("東葛線1")
        await page.locator("#record-results summary").first.click()
        content = await page.locator("#record-results").inner_text()
        assert "290 MW" in content and "62 MW" in content and "Not published" in content
        await page.locator("#records-panel > summary").click()
        # A failed week change must keep the selector and visible data aligned.
        await page.unroute("**/data/snapshots/2020-W01.json")
        await page.route("**/data/snapshots/2020-W01.json", lambda route: route.fulfill(status=503, body="unavailable"))
        await page.locator("#snapshot-week").select_option(prior["week"])
        await page.wait_for_function("document.getElementById('dataset-status').textContent.includes('Still displaying')")
        assert await page.locator("#snapshot-week").input_value() == current["week"]
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.locator("#toggle-panel").click()
        assert not await page.locator("#panel").is_visible()
        await page.locator("#toggle-panel").click()
        assert await page.locator("#panel").is_visible()
        assert not errors, errors
        await browser.close()
    scope = "ten regions, " if not os.environ.get('GRID_SKIP_REGIONS') else ""
    print("PASS: " + scope + "historical values/geometry, source failure, filters, record search, basemap and mobile controls", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
