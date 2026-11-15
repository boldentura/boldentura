#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub contribution calendar.

Run daily by .github/workflows/profile-cards.yml before render_cards.py.
Needs GH_TOKEN (the workflow passes its own GITHUB_TOKEN).

Commits to private repos (the bolden-dev org) appear in the calendar only when
"Include private contributions on my profile" is on in GitHub profile settings.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "profile.json").read_text())
OUT = ROOT / "data" / "contributions.json"
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}"""


def main() -> None:
    if not TOKEN:
        sys.exit("Set GH_TOKEN or GITHUB_TOKEN")
    to = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    frm = to - dt.timedelta(days=364)
    body = {"query": QUERY, "variables": {"login": CONFIG["login"], "from": frm.isoformat(), "to": to.isoformat()}}
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "profile-cards"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GraphQL request failed: HTTP {e.code}")
    if data.get("errors"):
        sys.exit(f"GraphQL errors: {data['errors']}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [{"date": d["date"], "count": d["contributionCount"]} for w in weeks for d in w["contributionDays"]]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "login": CONFIG["login"],
        "fetched_at": to.isoformat(),
        "days": days,
    }, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(days)} days, {sum(d['count'] for d in days)} contributions")


if __name__ == "__main__":
    main()
