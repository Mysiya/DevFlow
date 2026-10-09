"""Optional read-only integration smoke test against a public GitHub repo."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import Settings
from app.github import GitHubClient


async def main():
    # Public smoke test uses no stored token or model key.
    client = GitHubClient(Settings(devflow_mode="live", github_token=""))
    try:
        snapshot = await client.snapshot("octocat/Hello-World")
        print({"repository": snapshot["name"], "source": snapshot["source"], "issues": len(snapshot["issues"]), "pulls": len(snapshot["pulls"]), "ci_runs": len(snapshot["runs"]), "documents": len(snapshot["documents"]), "readme_revision": snapshot["documents"][0]["revision"] if snapshot["documents"] else None})
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
