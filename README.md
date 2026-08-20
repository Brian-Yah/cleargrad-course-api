# ClearGrad Course API

A validated, versioned, static course-data API for ClearGrad. It keeps the
existing `NSYSUCourseAPI` row contract and URL layout so ClearGrad can switch
sources without rewriting its course parser.

> 感謝國立中山大學提供課程資料。

This project is an independent publication and validation pipeline. Its primary
collector queries the public NSYSU course system directly every 15 minutes,
limits official-site concurrency to two requests, cross-checks structure and
continuity, and publishes only snapshots that pass all safety gates.
[NSYSUCourseAPI](https://github.com/NSYSU-OpenDev/NSYSUCourseAPI) remains a
fallback source, not the primary data feed.

## Static API

The Pages site exposes:

```text
/version.json
/{semester}/version.json
/{semester}/{snapshot}/all.json
/{semester}/{snapshot}/all.raw.json
/{semester}/{snapshot}/info.json
/{semester}/{snapshot}/manifest.json
/{semester}/{snapshot}/validation.json
/{semester}/{snapshot}/diff.json
/lkg/{semester}/all.json
/lkg/{semester}/manifest.json
/health.json
/schemas/*.json
```

`all.json` remains a bare array with the same fields ClearGrad currently
expects, with provably redundant rows canonicalized. `all.raw.json` preserves
the complete upstream array byte-for-byte at the JSON-value level for audit and
reprocessing. Exact copies collapse automatically; description-only variants
keep the most informative description; every other conflict remains visible
and is reported instead of guessed. Provenance, both checksums, schema versions,
and validation results live in adjacent files.

## Publication safety gates

A fetched snapshot is rejected when any of the following is true:

- the payload is not a JSON array;
- fewer than 500 course sections are present;
- required compatibility fields are missing;
- a course id/name is empty or `classTime` is not a seven-day array;
- numeric enrollment/capacity fields are invalid;
- more than 5% of section identities are duplicated;
- the row count falls more than 10% from the previous accepted snapshot;
- stable section identities or populated class-time coverage collapse;
- a fallback candidate loses a significant share of one opening department,
  even when unrelated rows keep the total course count unchanged;
- fallback `restrict`, `select`, `selected`, or `remaining` coverage collapses
  globally or within an opening department;
- NSYSUCourseAPI attempts to establish a new semester without a previously
  retained direct-official baseline;
- the same source version unexpectedly changes checksum.

Rejected data never replaces `/version.json` or `/lkg/...`. The existing Pages
deployment remains the last-known-good snapshot.

NSYSUCourseAPI is treated as an untrusted emergency candidate rather than an
authoritative copy. Every changed fallback snapshot is compared with the newest
durable `/official-baseline/{semester}/` direct snapshot using stricter
whole-catalog, department, and enrollment-field continuity gates. A complete
direct-official snapshot may
legitimately add, modify, or remove courses; accepted removals remain visible in
the versioned `diff.json`, `diff.txt`, and manifest warnings.

## Update policy

The workflow runs at minute 2, 17, 32, and 47 of each hour. Offsetting the
schedule from the top of the hour avoids the busiest GitHub Actions window. It
queries the official NSYSU course system first with at most two concurrent
requests. If that collector fails, it tries the NSYSUCourseAPI Pages endpoint
and then its raw `gh-pages` representation. Older fallback data is never
allowed to replace a newer last-known-good snapshot.

The root upstream index is also used for automatic semester discovery. On the
first successful run, every missing semester is backfilled once with its latest
validated snapshot. Later runs update the newest semester and backfill only a
newly discovered semester, so historical catalogs do not generate repeated
network traffic. Third-semester summer catalogs use a 10-row safety floor;
regular catalogs use 500 rows.

## Retention

- every discovered semester keeps at least its final/latest validated snapshot
  indefinitely;
- the active semester keeps the newest five content-changing snapshots;
- both canonical `all.json` and complete `all.raw.json` are retained for those
  snapshots;
- retention is version-count based, not day based;
- a semester is not removed merely because it disappears from the latest
  upstream index—the hydrated published root remains authoritative for archive
  discovery.

Five hot snapshots bound Pages size and hydration traffic while the permanent
per-semester snapshot keeps old planning data available. Supabase remains a
separate cold backup and is not involved in the 15-minute discovery job.

Supabase is deliberately excluded from this 15-minute workflow. It is a cold
backup, not another live mirror. See
[ClearGrad integration](docs/cleargrad-integration.md) for the read/write
policy.

## Local verification

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\cleargrad-course-api sync --output site
.venv\Scripts\cleargrad-course-api audit --output site
```

The sync command performs network requests. Tests use local fixtures and do not
contact NSYSU, GitHub Pages, or Supabase.

## GitHub Pages setup

1. Create a public repository named `cleargrad-course-api`.
2. Push this project to `main`.
3. Open **Settings → Pages** and select **GitHub Actions** if Pages was not
   automatically enabled by the first workflow run.
4. Run **Sync and deploy course API** once with `workflow_dispatch`.
5. Confirm `/health.json`, then point ClearGrad's primary course source to the
   Pages URL.

No Vercel Serverless Function is used.

## Data status

This API is a planning aid and not an official NSYSU service. Users must verify
enrollment-critical information against the university system.
