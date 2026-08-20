# My Douban Archive

Personal Douban movie and book archive powered by GitHub Actions, with a
separate conservative Douban-to-TMDb identity archive.

This project only stores your own Douban records and public title metadata. It
does not connect to Emby, Trakt, Bark, or any local service from the parent
project.

## What It Saves

- Category: `movie` or `book`
- Media type for movie-channel records: `movie` or `tv`
- Status: `wish`, `do`, or `collect`
- Douban subject ID
- Title
- Douban subject URL
- Your own rating, if present
- Your own short comment, if present
- Your own tags, if present
- `marked_at`, the date you marked the item on Douban, if present

The archive has one top-level `generated_at` timestamp for the file generation
time. Individual records do not store repeated archive timestamps.

For Douban's movie channel, the archive fetches movie and TV list filters
separately with `type=movie` and `type=tv`, then saves that list type as
`media_type`. It does not visit subject detail pages to infer metadata.

It does not save cookies, cover image files, full Douban descriptions, other users'
comments, private messages, group content, rating distributions, or subject
detail-page content.

## TMDb Identity Archive

`data/douban_tmdb.json` maps movie-channel Douban IDs to stable TMDb identities.
It is public, just like `data/douban.json`. Resolved records may include the
Douban title/type, resolved movie/TV type, TMDb ID/title/year/poster path, IMDb
ID, and the matching method. It never stores Emby IDs, local paths, usernames,
cookies, or API keys.

New subjects are handled conservatively:

1. Fetch the subject detail page with the existing logged-in Douban session.
2. If the page exposes an IMDb ID, use TMDb's external-ID find endpoint.
3. Without IMDb, search both movie and TV and accept only a unique exact
   title/original-title candidate with a compatible year.
4. Store uncertain matches as `unresolved`; never select the first fuzzy result.
5. Store temporary Douban/TMDb failures as `pending` so scheduled runs retry them.

Scheduled runs do not retry `unresolved` records unless an override is added.
Use the TMDb workflow's `retry_unresolved` manual option after changing matching
evidence or overrides. Known exceptions belong in `config/tmdb_overrides.json`.

## GitHub Secrets

Add these repository secrets in GitHub:

- `DOUBAN_COOKIE`: the full Cookie value copied from your logged-in browser.
- `DOUBAN_USER`: your Douban user ID, for example `188332994`.
- `TMDB_API_KEY`: a TMDb API key used only by the separate enrichment workflow.

Never commit cookies or API keys into this repository.

## Local Test

```bash
cd my-douban-archive
export DOUBAN_COOKIE='your full cookie here'
export DOUBAN_USER='your douban user id'
python -m pip install -r requirements.txt
python scripts/selftest_archive.py
python scripts/selftest_tmdb.py
python scripts/archive_douban.py --max-pages 1
```

The archive will be written to `data/douban.json`.

## GitHub Actions

The Douban workflow at `.github/workflows/archive.yml` supports:

- Manual runs through `workflow_dispatch`.
- Scheduled runs every 3 hours.
- Automatic commits only when `data/douban.json` changes.

TMDb enrichment runs independently through
`.github/workflows/enrich_tmdb.yml`. It listens for completion of the Douban
workflow, compares that run's starting commit with the latest `main`, and runs
the enrichment job only when `data/douban.json` actually changed. Manual runs
remain available. It commits only `data/douban_tmdb.json`; a failure in either
workflow does not fail or cancel the other workflow.

If Douban returns 403, a captcha, or a login page, the script stops and asks you
to refresh `DOUBAN_COOKIE`. It does not try to bypass access checks.

## Failure Cases

- First run creates a new `data/douban.json`: the workflow stages the file before
  checking for changes, so new files are committed correctly.
- No real archive changes: the script keeps record content stable, so the
  workflow does not create noisy scheduled commits.
- Cookie missing or expired: the workflow fails before archiving, or the script
  stops on login/captcha/403 responses. Refresh `DOUBAN_COOKIE` in repository
  secrets.
- Douban temporarily fails: the script retries temporary network/server errors,
  then stops if the request still fails.
- Parser finds zero records: the script fails by default so a broken page or
  changed Douban layout does not overwrite your archive with empty data.
- Two runs overlap: workflow concurrency queues runs for the same branch.
- Remote branch changes during the run: the workflow rebases before pushing the
  archive commit.
- Secret leakage: the script validates the output before writing and fails if a
  cookie value appears in the archive JSON.
- Parser regressions: the workflow runs `scripts/selftest_archive.py` before
  accessing Douban.
- Movie/TV classification: movie-channel lists are fetched separately with
  Douban's `type=movie` and `type=tv` filters; each fresh movie-channel record
  must include `media_type`.
- Douban UI text in comments: date/status/control text such as `读过 修改 删除`
  is stripped; if it still leaks into output, validation fails before writing.
- Obsolete per-record archive timestamps: `first_seen_at`, `updated_at`, and
  `last_seen_at` are removed during merge and rejected during validation.
