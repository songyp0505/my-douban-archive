# My Douban Archive

Personal Douban movie and book archive powered by GitHub Actions.

This project only stores your own Douban records. It does not connect to Emby,
TMDb, Trakt, Bark, or any local service from the parent project.

## What It Saves

- Category: `movie` or `book`
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

It does not save cookies, cover images, full Douban descriptions, other users'
comments, private messages, group content, or rating distributions.

## GitHub Secrets

Add these repository secrets in GitHub:

- `DOUBAN_COOKIE`: the full Cookie value copied from your logged-in browser.
- `DOUBAN_USER`: your Douban user ID, for example `188332994`.

Never commit cookies or API keys into this repository.

## Local Test

```bash
cd my-douban-archive
export DOUBAN_COOKIE='your full cookie here'
export DOUBAN_USER='your douban user id'
python -m pip install -r requirements.txt
python scripts/selftest_archive.py
python scripts/archive_douban.py --max-pages 1
```

The archive will be written to `data/douban.json`.

## GitHub Actions

The workflow at `.github/workflows/archive.yml` supports:

- Manual runs through `workflow_dispatch`.
- Scheduled runs every 3 hours.
- Automatic commits only when `data/douban.json` changes.

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
- Douban UI text in comments: date/status/control text such as `读过 修改 删除`
  is stripped; if it still leaks into output, validation fails before writing.
- Obsolete per-record archive timestamps: `first_seen_at`, `updated_at`, and
  `last_seen_at` are removed during merge and rejected during validation.
