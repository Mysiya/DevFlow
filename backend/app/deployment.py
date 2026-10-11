"""Nonsecret, validated metadata supplied by the hosting platform."""
import os
import re


def deployment_metadata():
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    deployment = os.environ.get("RAILWAY_DEPLOYMENT_ID", "")
    return {
        "git_commit_sha": sha.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else None,
        "deployment_id": deployment.lower() if re.fullmatch(
            r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", deployment
        ) else None,
    }
