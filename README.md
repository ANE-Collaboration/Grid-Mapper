# Grid Mapper · Japan

A static map of Japan's mapped power lines, linked to weekly disclosures from all ten regional transmission and distribution utilities. It runs on GitHub Pages or Cloudflare Pages; the weekly Python collector runs in GitHub Actions.

The first live collection, **2026-W37**, contains **36,867 OSM way segments**, **12,038 TSO line records**, **3,467 confirmed segment matches**, and **2,012 segments with possible matches**. Only **three confirmed segments** currently have numeric published capacity. This is a coverage limitation, not a scraper failure: most high-voltage disclosures omit capacity, and many OSM ways lack the identifying tags needed for a reliable match.

## Use the map

- The latest collected week opens by default. Select an earlier week to see its records, collection status, source files and geometry. A historical link includes `?week=2026-W37`.
- Search and filter mapped lines by name, voltage, published capacity and match status.
- Click a line for source-linked values. **Browse all published records** also includes disclosures without a confirmed map location.
- **Sources & coverage** shows failed or stale collectors. A failed utility retains its previous records and previous successful-collection date.
- Download the full JSON for the selected week. Earlier weeks appear as collection history accumulates; no historical observations are invented.

### What the colours mean

| Colour | Meaning |
| --- | --- |
| Green | Confirmed match with published capacity above 0 MW |
| Red | Confirmed match with published capacity equal to 0 MW |
| Blue | Confirmed TSO record, but numeric capacity is not published |
| Amber | Possible match; ownership, circuit or section is unconfirmed |
| Grey | No matched TSO record |

A zero value is not a declaration that a connection is impossible. An operating limit, forecast flow or N−1 control allowance is not spare connection capacity. The app preserves these as separate fields. Where an upstream-constrained capacity column exists, its blank value stays unknown; the app does not substitute a more permissive equipment-only value.

Geometry covers the OSM `power=line`, `power=minor_line` and `power=cable` ways returned for Japan, including unknown and other voltages. OSM is not an exhaustive utility asset register. Separate ways may form one electrical line, and parallel ways do not represent additive connection capacity. The app therefore does not sum capacities across map segments.

## Deploy this repository to GitHub Pages

For the free trial, use the public repository **ANE-Collaboration/Grid-Mapper**.

1. Open **Settings → Pages** and choose **GitHub Actions** as the build/deployment source.
2. Open **Actions → Weekly grid update and deployment → Run workflow**. Select `main`. Leave **Deploy to GitHub Pages** enabled. The existing current-week snapshot is reused.
3. Wait for the build and deploy jobs to finish. The site will be at **https://ane-collaboration.github.io/Grid-Mapper/**.
4. Check **Settings → Actions → General** if an organization policy blocks Actions or the workflow's requested write permissions. The update job needs permission to commit files under `history/`; deployment needs Pages and OIDC permissions.

No API token, server, database, paid runner, custom domain or separate hosting account is required. Standard GitHub-hosted Actions runners are [free for public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions). [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) include a 1 GB site limit and a soft 100 GB/month bandwidth limit. Pages restricts commercial SaaS hosting; use another host if this becomes a commercial product.

The workflow runs every **Monday at 09:23 Japan time**. GitHub may delay scheduled jobs; inspect the Actions run and the date displayed in the app if an update is late. Scheduled workflows in inactive public repositories can be disabled by GitHub after 60 days without repository activity.

The update and deployment happen in the same workflow. This avoids relying on a second workflow being triggered by a commit made with `GITHUB_TOKEN`. A failed validation/build does not publish new data. A push conflict fails rather than overwriting concurrent changes; rerun the workflow after reviewing the new remote commit.

## Cloudflare Pages alternative

The same build works without changes to application code.

1. In Cloudflare, choose **Workers & Pages → Create → Pages → Connect to Git**.
2. Select this repository and production branch `main`.
3. Set framework preset **None**, root directory blank, build command `npm ci --prefix web && npm run build --prefix web`, and build output `web/dist`.
4. Set the GitHub repository Actions variable `GRID_HOST` to `cloudflare` to skip GitHub Pages deployment. The weekly collector still commits history, and Cloudflare's Git integration builds on pushes.

Do not add a Worker or R2 bucket for this version. [Cloudflare Pages Free limits](https://developers.cloudflare.com/pages/platform/limits/) include 500 builds/month, 20,000 files and 25 MiB per asset. The build validates asset size and file count. The initial data is about **11 MB total**, with a largest asset of **6.3 MB**.

History grows by roughly the weekly JSON size plus changed source documents; geometry is shared. The initial rate suggests several hundred MB for a year, depending on future source changes. Validation stops before 900 MiB or 19,000 files so storage can be migrated without deleting history.

## Local development

Python 3.11+ and Node.js 22 are recommended. From the repository root:

```powershell
python -m pip install -r pipeline/requirements-test.txt
python -m pytest tests/test_weekly_snapshot.py -q
python -m pipeline.validate_history
npm ci --prefix web
npm run dev --prefix web
```

The committed history makes an initial network scrape unnecessary. Build a static deployment with:

```powershell
npm run build --prefix web
npm run preview --prefix web
```

Create the current week's snapshot:

```powershell
python -m pipeline.weekly_snapshot
```

The command is idempotent: an existing ISO week is never overwritten. To refresh OSM when creating a **new** week:

```powershell
python -m pipeline.weekly_snapshot --refresh-geometry
```

Geometry otherwise reuses the preceding snapshot. OSM downloads fail explicitly instead of substituting a test fixture. A first bootstrap can also use the previously verified local `data/raw_osm_lines.geojson` cache. The `pipeline.update_grid` entry point delegates to this weekly pipeline.

## Data pipeline and history

```text
Official utility pages → discover current CSV/ZIP URLs → download + SHA-256
                           ↓
                  Parse source-specific CSV schemas
                           ↓
OSM geometry → conservative operator/name/voltage matching
                           ↓
history/snapshots/YYYY-Www.json → history/index.json → static map
          ↘ shared geometry + archived source documents
```

| Component | Responsibility |
| --- | --- |
| `pipeline/tso_sources.py` | Ten official landing pages, current-download discovery rules and minimum coverage checks |
| `pipeline/collect_tso.py` | HTTP retries, bounded downloads/ZIP reads, Japanese encodings, CSV parsing, per-source failure retention |
| `pipeline/match_capacity.py` | Operator/name/voltage matches; ambiguous sections and missing operators stay candidates |
| `pipeline/weekly_snapshot.py` | Immutable ISO-week JSON, geometry versions, index publication |
| `pipeline/validate_history.py` | Historical references, source checksums and static-host limits |
| `web/src/snapshots.ts` | Historical loading, gzip geometry and cancellation of superseded requests |
| `web/scripts/prepare-data.mjs` | Stage only versioned history for development/builds |

`history/` is committed, not an expiring Actions artifact or cache:

- `index.json`: available weeks and latest snapshot.
- `snapshots/YYYY-Www.json`: source health, normalized records, matching evidence and pinned geometry.
- `geometry/<sha256>.json.gz`: all OSM geometry coordinates and relevant tags, shared by snapshots.
- `sources/<sha256>.*`: original downloaded documents, deduplicated by content.

Each record identifies the utility, official URL through its document reference, source member, CSV row and publication date when available. Collection timestamps are separate from source publication dates. Missing, withheld, nonnumeric and negative capacity values remain null. Signed forecast flows are retained.

Headers and download counts are validated. A reduction of over 30% in a utility's records, an unrecognized schema, or a failed file causes that utility's previous successful collection to be retained as stale. If all utilities fail, no new snapshot is published. No fuzzy name-only or highest-capacity duplicate selection is used.

## Official sources

[OCCTO's utility directory](https://www.occto.or.jp/institution/access/link/mapping.html) points to the regional disclosures:

| Utility | Public source used |
| --- | --- |
| Hokkaido | [Forecast/capacity ZIPs](https://www.hepco.co.jp/network/con_service/public_document/bid_info.html) |
| Tohoku | [CSV links in the regional image map](https://nw.tohoku-epco.co.jp/consignment/system/announcement/index.html) |
| TEPCO | [Regional forecast-flow CSV ZIPs](https://www.tepco.co.jp/pg/consignment/system/index-j.html) |
| Chubu | [Public map's CSV download manifest](https://gridmap.powergrid.chuden.co.jp/) |
| Hokuriku | [Transmission-line CSVs](https://www.rikuden.co.jp/nw_notification/U_154seiyaku.html) |
| Kansai | [Above/below 154 kV line CSVs](https://www.kansai-td.co.jp/consignment/disclosure/distribution-equipment/index.html) |
| Chugoku | [Backbone/regional CSV ZIPs](https://www.energia.co.jp/nw/service/retailer/keitou/access/) |
| Shikoku | [Backbone/regional line CSVs](https://www.yonden.co.jp/nw/line_access/data.html) |
| Kyushu | [Transmission-line CSV ZIP](https://www.kyuden.co.jp/td/service/wheeling/disclosure.html) |
| Okinawa | [Mainland/island line CSVs](https://www.okiden.co.jp/business-support/service/rule/plan/index.html) |

Downloads are discovered afresh each week; dated filenames are not hardcoded. No private endpoint or login is used. Transformer/fence tables accompanying line ZIPs are identified and excluded from line matching.

Map data: [OpenStreetMap contributors, ODbL](https://www.openstreetmap.org/copyright). Utility disclosures remain attributed to their publishers. Inspect their notes and obtain the utility's connection study before relying on any value for an actual connection.
