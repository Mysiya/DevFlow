import asyncio

from .checkpoints import CheckpointError
from .github import GitHubClient
from .workspace import WorkspaceManager
from .prompts import bound_settings


async def validate_sources(state, settings):
    """Check again when a queued resume starts, since a PR may change while waiting."""
    try:
        bound_settings(settings, state["input"])
    except ValueError as exc:
        raise CheckpointError(str(exc)) from exc
    workspace = state["input"]["workspace"]
    if workspace: await asyncio.to_thread(WorkspaceManager(settings).files, workspace)
    if state["input"]["mode"] == "live":
        github = GitHubClient(settings)
        try:
            for value in state["outcomes"].values():
                if value["agent"] == "pr" and value.get("pr_context"):
                    current = await github.get(f"/repos/{state['input']['snapshot']['name']}/pulls/{value['target']}")
                    if current["head"]["sha"] != value["pr_context"]["head_sha"]:
                        raise CheckpointError("已分析 PR 的 head SHA 已变化，请重新分析，不能复用旧 PR 结论。")
        finally: await github.close()
