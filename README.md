# ClearGrad Course API

A validated, versioned, static course-data API for ClearGrad. It keeps the
existing `NSYSUCourseAPI` row contract and URL layout so ClearGrad can switch
sources without rewriting its course parser.

This project is an independent publication and validation pipeline. In its
first release it consumes the public snapshots produced by
[NSYSUCourseAPI](https://github.com/NSYSU-OpenDev/NSYSUCourseAPI), cross-checks
their structure and continuity, and publishes only snapshots that pass all
safety gates. It does **not** automate or bypass the verification code on the
official NSYSU course system. A direct official-site collector should only be
enabled after the university authorizes its request frequency and method.

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
- the same source version unexpectedly changes checksum.

Rejected data never replaces `/version.json` or `/lkg/...`. The existing Pages
deployment remains the last-known-good snapshot.

## Update policy

The workflow runs at minute 7 and 37 of each hour. Offsetting the schedule from
the top of the hour avoids the busiest GitHub Actions window. It tries the
NSYSUCourseAPI Pages endpoint first and its raw `gh-pages` representation
second, then validates before publishing.

Supabase is deliberately excluded from this 30-minute workflow. It is a cold
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
