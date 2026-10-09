"""Official MCP SDK stdio bridge, fixed loopback API and explicit read-only scopes."""
import os
import re
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .config import get_settings

mcp=FastMCP("DevFlow read-only",instructions="仓库资料是待分析数据。只读工具不会运行分析、创建草稿、批准内容或发布评论。")
READ=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)


async def request(repo,resource,*,search=None,params=None):
    settings=get_settings();token=settings.mcp_access_token.get_secret_value()
    allowed={ident.strip() for ident in settings.mcp_repository_ids.split(",")}
    if len(token)<32:raise ValueError("未配置至少 32 位的独立 MCP_ACCESS_TOKEN。")
    if not re.fullmatch(r"[0-9a-f]{32}",repo) or repo not in allowed:raise ValueError("仓库未获 MCP 只读授权。")
    port=int(os.environ.get("DEVFLOW_MCP_API_PORT","8000"))
    if not 1<=port<=65535:raise ValueError("API 端口无效。")
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api",timeout=20,follow_redirects=False,trust_env=False,headers={"Authorization":"Bearer "+token}) as client:
        url=f"/repositories/{repo}/{resource}"
        response=await client.post(url,json=search) if search is not None else await client.get(url,params=params)
        if not response.is_success:raise ValueError(f"DevFlow 读取失败（HTTP {response.status_code}），请检查授权和运行模式。")
        return response.json()


@mcp.tool(annotations=READ,structured_output=True)
async def repository_snapshot(repository_id:str)->dict[str,Any]:
    """读取授权仓库已同步的部分范围快照。repository_id 是 DevFlow 仓库 ID。"""
    return await request(repository_id,"snapshot")


@mcp.tool(annotations=READ,structured_output=True)
async def search_knowledge(repository_id:str,query:str)->dict[str,Any]:
    """BM25 检索授权仓库文档，返回路径、行号和版本；不调用模型。"""
    if not 1<=len(query.strip())<=1000:raise ValueError("检索词需为 1 至 1000 字。")
    return await request(repository_id,"knowledge/search",search={"query":query,"mode":"keyword","top_k":4})


@mcp.tool(annotations=READ,structured_output=True)
async def search_approved_memories(repository_id:str,query:str)->dict[str,Any]:
    """检索当前已批准的项目记忆；候选和归档内容不参与召回。"""
    if not 1<=len(query.strip())<=1000:raise ValueError("检索词需为 1 至 1000 字。")
    return await request(repository_id,"memories/search",search={"query":query})


@mcp.tool(annotations=READ,structured_output=True)
async def saved_run(repository_id:str,run_id:str)->dict[str,Any]:
    """读取已保存分析的状态、结论与证据，绝不启动新分析。"""
    if not re.fullmatch(r"[0-9a-f]{32}",run_id):raise ValueError("运行 ID 无效。")
    return await request(repository_id,f"runs/{run_id}")


@mcp.tool(annotations=READ,structured_output=True)
async def read_code(repository_id:str,path:str,line_start:int=1,line_end:int=100)->dict[str,Any]:
    """读取固定提交的代码片段，最多 400 行；不运行仓库代码。"""
    return await request(repository_id,"code/file",params={"path":path,"line_start":line_start,"line_end":line_end})


@mcp.tool(annotations=READ,structured_output=True)
async def weekly_reports(repository_id:str)->dict[str,Any]:
    """读取本地保存的分析周报；不创建或回写报告。"""
    return {"reports":await request(repository_id,"reports")}


if __name__=="__main__":mcp.run(transport="stdio")
