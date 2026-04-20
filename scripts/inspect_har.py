"""Inspect a HAR file exported from the panel and summarise endpoints.

Usage:
    python scripts/inspect_har.py captures/panel.har

The output helps to wire up `viking.api.client.VikingClient` against the real
endpoints (auth, list menu, change dish, save order).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit


INTERESTING_HOST_HINTS = ("kuchniavikinga", "ml-assets", "panel")


def _is_interesting(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(h in host for h in INTERESTING_HOST_HINTS)


def _short(body: str, limit: int = 240) -> str:
    body = body.strip().replace("\n", " ")
    return body if len(body) <= limit else body[:limit] + "…"


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 1

    by_endpoint: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for p in paths:
        har = json.loads(Path(p).read_text(encoding="utf-8"))
        for entry in har.get("log", {}).get("entries", []):
            req = entry.get("request", {})
            res = entry.get("response", {})
            url = req.get("url", "")
            if not _is_interesting(url):
                continue
            mime = (res.get("content", {}).get("mimeType") or "").split(";")[0]
            if mime and mime not in {"application/json", "text/html", "text/plain"}:
                continue
            method = req.get("method", "?")
            split = urlsplit(url)
            path = split.path or "/"
            key = (method, path)
            by_endpoint[key].append(
                {
                    "status": res.get("status"),
                    "mime": mime,
                    "query": split.query,
                    "req_body": _short(req.get("postData", {}).get("text", "") or ""),
                    "res_body": _short(res.get("content", {}).get("text", "") or ""),
                }
            )

    if not by_endpoint:
        print("No interesting entries found in HAR.")
        return 0

    for (method, path), calls in sorted(by_endpoint.items()):
        print(f"\n=== {method} {path}  ({len(calls)} call(s)) ===")
        sample = calls[0]
        if sample["query"]:
            print(f"  query: {sample['query']}")
        if sample["req_body"]:
            print(f"  req:   {sample['req_body']}")
        print(f"  res ({sample['status']} {sample['mime']}): {sample['res_body']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
