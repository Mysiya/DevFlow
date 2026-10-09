"""Read-only connection checks. Never prints credentials or HTTP response bodies."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import Settings
from app.github import GitHubClient
import httpx


async def main():
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / "backend" / ".env")
    github = GitHubClient(settings)
    try:
        snapshot = await github.snapshot("Mysiya/DevFlow")
        print(json.dumps({"github": "ok", "repository": snapshot["name"], "documents": len(snapshot["documents"]), "issues": len(snapshot["issues"]), "pulls": len(snapshot["pulls"]), "runs": len(snapshot["runs"])}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"github": "failed", "reason": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}, ensure_ascii=False))
    finally:
        await github.close()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(settings.llm_base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()})
            print(json.dumps({"deepseek_http": response.status_code, "model_available": any(x.get("id") == settings.llm_model for x in response.json().get("data", [])) if response.is_success else False}))
    except httpx.HTTPError:
        print(json.dumps({"deepseek": "network error"}))


asyncio.run(main())
