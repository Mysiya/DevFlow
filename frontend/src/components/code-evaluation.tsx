"use client";

type Range = { path: string; line_start: number; line_end: number };
type Row = { id: string; query: string; path_prefix: string; expected: Range[]; retrieved: Range[]; passed: boolean };
export type CodeEvaluationResult = {
  dataset: string; fingerprint: string; scorer: string; description: string; current_strategy: string; positive_cases: number;
  strategies: Record<string, { rows: Row[]; mean: { hit_at_1: number; recall_at_5: number; mrr_at_5: number }; passed: number; total: number }>;
};
const names: Record<string, string> = {
  "python-definition": "Python 函数与装饰器", "webhook-definition": "定义优先于文档提及",
  "camel-method": "驼峰方法名拆词", "snake-function": "下划线函数名拆词",
  "typescript-camel": "TypeScript 标识符", "typescript-snake-query": "不同命名风格查询",
  "exact-path": "完整文件路径", "chinese-comment": "中文注释",
  "code-abstention": "无匹配结果", "directory-isolation": "限定目录隔离",
};
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const range = (r: Range) => `${r.path} L${r.line_start}–L${r.line_end}`;

export function CodeEvaluation({ result }: { result: CodeEvaluationResult }) {
  return <section className="panel governance-card">
    <div className="panel-heading"><h3>源码检索对照</h3><span className="tag">{result.dataset}</span></div>
    <p>用 10 条固定问题比较旧版行关键词与当前函数、片段检索。命中必须包含标注的完整行段。</p>
    <div className="delivery-table-scroll"><table className="delivery-table">
      <thead><tr><th>源码策略</th><th>Top 1 命中</th><th>Recall@5</th><th>MRR@5</th><th>通过</th></tr></thead>
      <tbody>{Object.entries(result.strategies).map(([key, value]) => <tr key={key}>
        <td>{key === result.current_strategy ? "BM25 + 标识符 + Python 定义" : "v0.9 行关键词基线"}</td>
        <td>{percent(value.mean.hit_at_1)}</td><td>{percent(value.mean.recall_at_5)}</td><td>{percent(value.mean.mrr_at_5)}</td><td>{value.passed} / {value.total}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="small-muted">均值只含 {result.positive_cases} 条有来源的问题，另外验证无匹配和目录隔离。数据版本 {result.fingerprint.slice(0, 12)}；这些人工样例的成绩不代表真实模型质量。</p>
    <div className="delivery-cases">{result.strategies[result.current_strategy].rows.map(row => <details key={row.id}>
      <summary><span className={`tag ${row.passed ? "review-approved" : "review-rejected"}`}>{row.passed ? "通过" : "未通过"}</span>{names[row.id] || row.id}</summary>
      <p>{row.query}{row.path_prefix ? ` · 目录 ${row.path_prefix}` : ""}</p>
      <p>标准来源：{row.expected.map(range).join("、") || "应无命中"}</p>
      <p>当前命中：{row.retrieved.map(range).join("、") || "无"}</p>
      <p className="small-muted">旧版命中：{result.strategies.line_keyword_v09.rows.find(r => r.id === row.id)?.retrieved.map(range).join("、") || "无"}</p>
    </details>)}</div>
  </section>;
}
