from copy import deepcopy

DEMO_REPO = "demo/devflow-shop"
DEMO_SHA = "a1b2c3d4e5f600000000000000000000000000000"

SNAPSHOT = {
    "name": DEMO_REPO,
    "description": "电商订单服务 · 自带演示仓库，不对应真实 GitHub 仓库",
    "branch": "main",
    "source": "demo",
    "coverage": "自带样例全集；所有数据和分析均用于演示",
    "issues": [
        {"number": 42, "title": "订单接口存在跨租户访问风险", "state": "open", "labels": ["bug", "security"], "body": "登录用户可以通过 /orders/{id} 读取其他租户订单。预期：必须校验订单的 tenant_id。影响订单隐私。", "url": "", "updated_at": "2026-10-06T01:00:00Z"},
        {"number": 43, "title": "CI 中 PostgreSQL 连接间歇性失败", "state": "open", "labels": ["ci"], "body": "偶发 Connection refused；检查数据库启动就绪探针，避免固定 sleep。", "url": "", "updated_at": "2026-10-05T02:00:00Z"},
        {"number": 44, "title": "补充本地开发启动文档", "state": "open", "labels": ["documentation"], "body": "补充环境变量、数据库初始化和测试命令。", "url": "", "updated_at": "2026-10-04T03:00:00Z"},
    ],
    "pulls": [
        {"number": 18, "title": "修复订单查询与权限校验", "state": "open", "body": "关联 Issue #42，调整订单查询。", "head_sha": DEMO_SHA, "base_sha": "0000000000000000000000000000000000000000", "url": "", "draft": False},
        {"number": 19, "title": "为订单列表增加组合索引", "state": "open", "body": "增加 tenant_id 和 created_at 索引。", "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "base_sha": "0000000000000000000000000000000000000000", "url": "", "draft": False},
    ],
    "runs": [
        {"id": 301, "name": "Backend tests", "status": "completed", "conclusion": "failure", "head_sha": DEMO_SHA, "url": "", "created_at": "2026-10-06T03:00:00Z"},
        {"id": 302, "name": "Lint & types", "status": "completed", "conclusion": "success", "head_sha": DEMO_SHA, "url": "", "created_at": "2026-10-06T03:00:00Z"},
        {"id": 303, "name": "Backend tests", "status": "completed", "conclusion": "success", "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "url": "", "created_at": "2026-10-05T03:00:00Z"},
    ],
    "documents": [
        {"id": "doc:tenant", "title": "订单服务 · 租户隔离约定", "content": "所有订单查询必须同时限定订单 ID 和当前用户 tenant_id。跨租户访问返回 404。禁止仅用主键查询后直接返回。新增接口必须覆盖租户隔离回归测试。", "url": ""},
        {"id": "doc:release", "title": "发布检查清单", "content": "发布前检查 security Issue、待合并 PR 和对应 head SHA 的 CI。失败检查应先修复。数据库迁移必须确认回滚路径。CI 未运行或证据不完整时不能断言可以发布。", "url": ""},
        {"id": "doc:ci", "title": "CI 数据库就绪策略", "content": "PostgreSQL 容器通过 pg_isready 健康检查后再执行集成测试。Connection refused 通常需要核对服务地址和数据库就绪时机。AssertionError 应先检查业务断言和实际结果。", "url": ""},
    ],
}


def demo_snapshot():
    return deepcopy(SNAPSHOT)


def demo_pr(number: int):
    pr = next((deepcopy(x) for x in SNAPSHOT["pulls"] if x["number"] == number), None)
    if pr is None:
        raise ValueError(f"演示 PR #{number} 不存在")
    if number == 18:
        pr["files"] = [{"filename": "app/orders/service.py", "status": "modified", "additions": 2, "deletions": 3, "patch": "@@ -18,5 +18,4 @@ def get_order(order_id, current_user):\n-    order = query.filter_by(id=order_id, tenant_id=current_user.tenant_id).first()\n+    order = query.filter_by(id=order_id).first()\n     if not order:\n         raise NotFound()\n     return order"}]
    else:
        pr["files"] = [{"filename": "migrations/002_orders_index.sql", "status": "added", "additions": 1, "deletions": 0, "patch": "+CREATE INDEX idx_orders_tenant_created ON orders (tenant_id, created_at);"}]
    pr["checks"] = [deepcopy(x) for x in SNAPSHOT["runs"] if x["head_sha"] == pr["head_sha"]]
    pr["coverage"] = "演示 PR 文件和当前 head SHA 对应的 Actions 结果"
    return pr


def demo_ci(run_id: int):
    run = next((deepcopy(x) for x in SNAPSHOT["runs"] if x["id"] == run_id), None)
    if run is None:
        raise ValueError(f"演示 CI #{run_id} 不存在")
    run["logs"] = "tests/test_orders.py::test_cross_tenant_read FAILED\nassert response.status_code == 404\nE AssertionError: assert 200 == 404\n1 failed, 23 passed" if run_id == 301 else "All checks passed"
    run["log_gaps"] = []
    return run
