"use client";
export type FactEvaluationResult={dataset:string;fingerprint:string;description:string;passed:number;total:number;rows:{id:string;label:string;expected:string;observed:string;passed:boolean;checks:Record<string,boolean>}[]};
const states:Record<string,string>={matched:"与来源一致",conflict:"源码事实冲突",insufficient:"未验证"};
export function FactEvaluation({result}:{result:FactEvaluationResult}){
  return <section className="panel governance-card"><div className="panel-heading"><h3>源码事实核对回归</h3><span className="tag">{result.passed} / {result.total}</span></div>
    <p>{result.description}</p><p className="small-muted">覆盖参数、有限数学关系、源码写法、否定与建议、作用域、引用与版本隔离。仅验证检查器行为；不测回答的整体正确性。数据版本 {result.fingerprint.slice(0,12)}。</p>
    <div className="delivery-cases">{result.rows.map(row=><details key={row.id}><summary><span className={`tag ${row.passed?"review-approved":"review-rejected"}`}>{row.passed?"通过":"未通过"}</span>{row.label}</summary><p>预期：{states[row.expected]} · 实际：{states[row.observed]}</p>{!row.passed&&<p>{Object.entries(row.checks).filter(([,v])=>!v).map(([k])=>k).join("、")}</p>}</details>)}</div>
  </section>;
}
