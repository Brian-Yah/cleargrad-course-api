# ClearGrad integration and fallback policy

## Resolution order

For an active semester, ClearGrad should resolve exactly one source at a time:

1. **ClearGrad Course API** — primary validated static snapshot.
2. **NSYSUCourseAPI** — compatibility fallback when the primary is unavailable,
   stale, or invalid.
3. **Supabase `global_course_catalog`** — cold last-known-good fallback only
   after both network sources fail.

All three results pass the same client-side acceptance checks before entering
the application cache: valid version document, valid array, expected semester,
minimum reasonable row count, required fields, and no abnormal duplicate or
row-count collapse.

## Supabase cold-backup rules

Supabase must not be polled during normal startup merely to merge semester
lists. It is consulted only when both static sources fail their availability or
validation gates.

Recommended read policy:

- **zero Supabase course-catalog requests while either static source is healthy**;
- one Supabase request per semester per browser session at most;
- an 8-second request timeout;
- cache a successful cold-backup result in IndexedDB for 24 hours;
- cache a failed lookup for 10 minutes to prevent retry storms;
- use a single in-flight promise so concurrent views do not duplicate queries;
- show `source=supabase-lkg` and `last_synced_at` in diagnostics.

Recommended write policy:

- run once daily, not every 30 minutes;
- first read the primary manifest and compare its SHA-256 with the most recent
  stored snapshot metadata;
- perform no course-row read or write when the checksum is unchanged;
- write only a snapshot whose `validationStatus` is `passed`;
- use an atomic staging/swap or snapshot id so a partial upload cannot replace
  the current backup;
- retain at least the newest two valid snapshots for recovery.

## Free-plan quota budget

The preferred cold-backup representation is one minified snapshot object per
semester in a public, read-only Supabase Storage bucket, accompanied by a tiny
manifest containing `snapshot_id`, `sha256`, `course_count`, and
`last_synced_at`. One outage then costs one manifest read and at most one object
download per browser session, instead of three or more PostgREST pages for a
2,000+ row catalog.

If the existing `global_course_catalog` table must remain the fallback source:

- do not query distinct semesters during application startup;
- do not subscribe with Realtime;
- use the existing 1,000-row pagination only after both static tiers fail;
- cache the assembled result in IndexedDB for 24 hours;
- share the same promise across all components and browser tabs when possible;
- stop immediately when one page fails rather than restarting from page zero;
- record the snapshot checksum separately so the daily writer can decide
  whether a bulk update is necessary without downloading every existing row.

The daily backup job should make one lightweight checksum lookup. If unchanged,
it exits with no row reads and no writes. If changed, it performs one staged
bulk replacement (or one Storage object upload), verifies the stored checksum,
and only then marks the new snapshot active. Cleanup of old snapshots should be
weekly, not part of every sync.

This makes Supabase operationally independent from a bad or incomplete live
publication and keeps it available for genuine outages.

## Circuit breaker

After two failures from the primary within five minutes, skip it for five
minutes and try NSYSUCourseAPI. After two failures from NSYSUCourseAPI, open its
circuit for ten minutes and permit one Supabase lookup. A successful probe
closes the relevant circuit.

Do not race all three tiers with `Promise.any`: it would hit Supabase during
healthy operation and could choose an older result merely because it responded
faster.

## Minimal configuration change

Keep the raw `APICourse` interface unchanged and replace the single
`courseApiBase` with ordered static bases:

```ts
courseApiBases: [
  'https://brian-yah.github.io/cleargrad-course-api',
  'https://nsysu-opendev.github.io/NSYSUCourseAPI',
]
```

The resolver should try the same paths on each base:

```text
/{semester}/version.json
/{semester}/{latest}/all.json
```

Only after both attempts fail should the existing paginated Supabase query run.
