"""Confirm the HTTPS server reports the expected deployed commit, without model calls.

Set DEVFLOW_ADMIN_USERNAME / DEVFLOW_ADMIN_PASSWORD in the process environment.
These credentials are sent only to the explicitly supplied HTTPS origin.
"""
import argparse
import json
import os
import re
import time
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        parser.error("--url must be an HTTPS origin without credentials, query or fragment")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.expected_sha):
        parser.error("--expected-sha must be a complete 40-character Git commit SHA")
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    username = os.environ.get("DEVFLOW_ADMIN_USERNAME", "admin")
    password = os.environ.get("DEVFLOW_ADMIN_PASSWORD", "")
    if not password:
        raise SystemExit("Set DEVFLOW_ADMIN_PASSWORD privately in the process environment")
    origin = args.url.rstrip("/")
    deadline = time.monotonic() + args.timeout
    with httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False,
                      timeout=10, follow_redirects=False) as client:
        signed_in = False
        try:
            while time.monotonic() < deadline:
                try:
                    if not signed_in:
                        login = client.post("/api/auth/login", json={"username": username, "password": password})
                        if login.status_code in (401, 403, 429):
                            raise SystemExit("Administrator login was rejected; deployment not verified")
                        login.raise_for_status()
                        signed_in = True
                    response = client.get("/api/admin/deployment")
                    if response.status_code in (401, 403):
                        raise SystemExit("Administrator access was rejected; deployment not verified")
                    if response.status_code == 200:
                        metadata = response.json()
                        if metadata.get("git_commit_sha") == args.expected_sha.lower():
                            health = client.get("/api/health")
                            health.raise_for_status()
                            if health.json().get("status") == "ok":
                                print(json.dumps({"verified": True, "expected_sha": args.expected_sha.lower(),
                                    "new_model_calls": 0}))
                                return
                except (httpx.HTTPError, ValueError):
                    pass  # Never print response bodies, credentials or signed URLs.
                time.sleep(min(5, max(0, deadline - time.monotonic())))
            raise SystemExit("Expected commit was not observed before timeout; deployment not verified")
        finally:
            if signed_in:
                try:
                    client.post("/api/auth/logout")
                except httpx.HTTPError:
                    pass


if __name__ == "__main__":
    main()
