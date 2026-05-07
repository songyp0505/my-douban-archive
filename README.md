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
- Archive timestamps

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
python scripts/archive_douban.py --max-pages 1
```

The archive will be written to `data/douban.json`.

## GitHub Actions

The workflow at `.github/workflows/archive.yml` supports:

- Manual runs through `workflow_dispatch`.
- Daily scheduled runs at `18:00 UTC`, roughly early morning in China.
- Automatic commits only when `data/douban.json` changes.

If Douban returns 403, a captcha, or a login page, the script stops and asks you
to refresh `DOUBAN_COOKIE`. It does not try to bypass access checks.
