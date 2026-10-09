"""Read-only public Git smoke test. Uses an isolated local store and no credentials."""
import asyncio
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "backend"))
from app.config import Settings
from app.github import GitHubClient
from app.workspace import WorkspaceManager, git


async def main():
    settings = Settings(_env_file=None, github_token="", llm_api_key="", workspace_root=str(project / "backend" / "data" / "code-smoke"))
    manager = WorkspaceManager(settings)
    client = GitHubClient(settings)
    try:
        name = "octocat/Hello-World"
        meta = await client.get(f"/repos/{name}")
        branch = await client.get(f"/repos/{name}/branches/{quote(meta['default_branch'], safe='')}")
        sha = branch["commit"]["sha"]
        ref = {"repository_id": uuid.uuid4().hex, "source": "github", "sha": sha}
        directory = manager.directory(ref["repository_id"], "github")
        await asyncio.to_thread(manager._init, directory)
        await asyncio.to_thread(git, ["fetch", "--depth=1", "--no-tags", f"https://github.com/{name}.git", sha], directory)
        files = manager.files(ref)
        hit = manager.read(ref, "README")
        matches = manager.search(ref, "Hello")
        assert "Hello" in hit["content"] and matches["results"][0]["sha"] == sha
        print({"repository": name, "sha": sha, "readable_files": len(files["files"]), "readme": hit["content"], "search_hits": len(matches["results"])})
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
