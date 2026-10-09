"use client";
import {useCallback,useEffect,useState} from "react";
import {Download,GitCompareArrows} from "lucide-react";
import {api,Evidence} from "@/lib/api";
import "./answer-comparison.css";
import {PromptExperimentPanel} from "./prompt-experiment";

type Prompt={id:string;name:string;description:string;template_hash:string;template:string};
export function AnalysisPromptSelect({repositoryId,value,onChange,disabled,demo}:{repositoryId:string;value:string;onChange:(value:string)=>void;disabled:boolean;demo:boolean}) {
  const [prompts,setPrompts]=useState<Prompt[]>([]),[error,setError]=useState("");
  useEffect(()=>{const controller=new AbortController();setPrompts([]);setError("");
    void api<{prompts:Prompt[]}>(`/repositories/${repositoryId}/analysis-prompts`,{signal:controller.signal}).then(data=>setPrompts(data.prompts)).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();},[repositoryId]);
  if(demo)return <span className="small-muted">演示规则分析</span>;
  return <><select aria-label="选择分析 Prompt" title={prompts.find(p=>p.id===value)?.description||error} value={value} onChange={e=>onChange(e.target.value)} disabled={disabled||!prompts.length}>
    {!prompts.length&&<option value="baseline-v1">{error?"Prompt 列表加载失败":"加载 Prompt…"}</option>}{prompts.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>{error&&<span role="alert" className="small-muted">{error}</span>}</>;
}

type Candidate={id:string;question:string;task:string;mode:string;provider:string;prompt_name:string|null;reviewable:boolean};
type Item={id:string;kind:string;text:string;evidence_ids:string[]};
type Annotation={item_id:string;decision:string;evidence_ids:string[];note:string};
type Side={run_id:string;created_at:string;task:string;snapshot:{question:string;mode:string;provider:string;items:Item[];evidence:Evidence[]};context:{prompt_name:string;prompt_id:string;template_hash:string;system_prompt:string;model_parameters:{model:string;max_tokens:number;temperature:number;reasoning_effort:string|null}}|null;context_problem:string|null;
  review:{version:number;annotations:Annotation[];created_by:string}|null;stale_review:boolean;
  metrics:{measured:boolean;model_calls:number;elapsed_ms:number|null;known_input_tokens:number;known_output_tokens:number;usage_complete:boolean;estimated_cost:string|null;currency:string|null}};
type Comparison={scope:string;left:Side;right:Side;controlled_pair:boolean;reasons:string[];winner:null;model_accuracy:null};
const names:Record<string,string>={title:"标题",summary:"摘要",recommendation:"建议",finding:"发现",next_steps:"下一步",gaps:"待确认项"};
const decisions:Record<string,string>={supported:"有证据支持",conflict:"与证据冲突",insufficient:"证据不足",not_applicable:"不作事实判断"};

function AnswerSide({data,label}:{data:Side;label:string}) {
  const m=data.metrics;
  return <section className="panel governance-card comparison-side"><div className="panel-heading"><h3>{label} · {data.context?.prompt_name||"Prompt 未记录"}</h3><span className="tag">{data.run_id.slice(0,8)}</span></div>
    <p className="comparison-question">{data.snapshot.question}</p><p className="small-muted">{data.task} · {data.snapshot.mode} · {data.snapshot.provider}</p>
    {data.context&&<details className="comparison-context"><summary>查看 Prompt 和模型参数</summary><p>{data.context.model_parameters.model} · 温度 {data.context.model_parameters.temperature} · 输出上限 {data.context.model_parameters.max_tokens} · 推理 {data.context.model_parameters.reasoning_effort??"服务默认"}</p><p className="small-muted">{data.context.prompt_id} · {data.context.template_hash.slice(0,12)}</p><pre>{data.context.system_prompt}</pre></details>}
    <p className="small-muted">{m.measured?`${m.model_calls} 次调用 · 耗时 ${m.elapsed_ms===null?"未知":`${m.elapsed_ms} ms`} · 已知输入 ${m.known_input_tokens} / 输出 ${m.known_output_tokens} Token${m.usage_complete?"":"（用量记录不完整）"}`:"历史运行没有完整用量记录"} · 费用 {m.estimated_cost===null?"未知":`${m.estimated_cost} ${m.currency||""}`}</p>
    <p className="review-note">人工标注 {data.review?.annotations.length||0} / {data.snapshot.items.length} 项{data.review?` · v${data.review.version} · ${data.review.created_by}`:""}{data.stale_review?" · 旧评审与当前回答不匹配，已排除":""}；未标注不表示正确。</p>
    {data.snapshot.items.map(item=>{const annotation=data.review?.annotations.find(a=>a.item_id===item.id);return <article className="comparison-item" key={item.id}><div><strong>{names[item.kind]||item.kind}</strong>{annotation&&<span className="tag">{decisions[annotation.decision]}</span>}</div><p>{item.text}</p>{item.evidence_ids.length>0&&<p className="small-muted">原引用：{item.evidence_ids.map(id=>data.snapshot.evidence.find(e=>e.id===id)?.citation||id).join(" · ")}</p>}{annotation&&<p className="review-note">{annotation.note}{annotation.evidence_ids.length>0&&` · 评审引用 ${annotation.evidence_ids.join("、")}`}</p>}</article>;})}
    <details className="comparison-context"><summary>当时的来源证据（{data.snapshot.evidence.length} 条）</summary>{data.snapshot.evidence.map(e=><details key={e.id}><summary>{e.citation||e.title||e.id}</summary><p className="small-muted">{e.id} · {e.source} · {e.sha||"无源码提交"}</p>{e.url&&<a href={e.url} target="_blank" rel="noreferrer">查看来源</a>}<pre>{e.content}</pre></details>)}</details>
  </section>;
}

export function AnswerComparisonPanel({repositoryId,canRun}:{repositoryId:string;canRun:boolean}) {
  const root=`/repositories/${repositoryId}`;
  const [runs,setRuns]=useState<Candidate[]>([]),[left,setLeft]=useState(""),[right,setRight]=useState(""),[data,setData]=useState<Comparison|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState("");
  const [preferred,setPreferred]=useState<{left:string;right:string}|null>(null);
  const showPair=useCallback((a:string,b:string)=>setPreferred({left:a,right:b}),[]);
  useEffect(()=>{const controller=new AbortController();setLoading(true);setData(null);setError("");
    void api<{runs:Candidate[]}>(root+"/answer-comparisons/runs",{signal:controller.signal}).then(async value=>{if(controller.signal.aborted)return;const available=value.runs.filter(r=>r.reviewable);
      if(preferred){const missing=[preferred.left,preferred.right].filter(id=>!available.some(r=>r.id===id));const extra=await Promise.all(missing.map(async id=>{const detail=await api<{id:string;question:string;task:string;status:string;result:{provider:string;analysis_context?:{prompt_name:string}}|null}>(root+`/runs/${id}`,{signal:controller.signal});if(detail.status!=="completed"||!detail.result)throw new Error("所选对照回答尚未完成。");return {id:detail.id,question:detail.question,task:detail.task,mode:"unknown",provider:detail.result.provider,prompt_name:detail.result.analysis_context?.prompt_name||null,reviewable:true};}));available.push(...extra);}
      if(controller.signal.aborted)return;setRuns(available);setLeft(preferred?.left||available[0]?.id||"");setRight(preferred?.right||available[1]?.id||"");setLoading(false);}).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false);}});
    return()=>controller.abort();},[root,preferred]);
  useEffect(()=>{setData(null);setError("");if(!left||!right||left===right){if(left&&right)setLoading(false);return;}const controller=new AbortController();setLoading(true);
    void api<Comparison>(root+`/answer-comparisons?left=${left}&right=${right}`,{signal:controller.signal}).then(value=>{if(controller.signal.aborted)return;setData(value);setLoading(false);}).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false);}});
    return()=>controller.abort();},[root,left,right]);
  return <><PromptExperimentPanel repositoryId={repositoryId} canRun={canRun} onReady={showPair}/><section className="panel governance-card"><div className="panel-heading"><h3><GitCompareArrows size={18}/>回答对照</h3>{data&&<a className="text-button" href={`/api${root}/answer-comparisons/export?left=${left}&right=${right}`} target="_blank" rel="noreferrer"><Download size={14}/>导出对照 JSON</a>}</div><p>选择最近 30 次已完成分析中的两次，查看原回答、当时来源和人工评审。查看和导出已有结果不会调用模型。</p>
    <div className="comparison-selects">{[{label:"左侧回答",value:left,change:setLeft},{label:"右侧回答",value:right,change:setRight}].map(s=><label key={s.label} className="delivery-select">{s.label}<select aria-label={s.label} disabled={loading} value={s.value} onChange={e=>s.change(e.target.value)}>{runs.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)} · {r.prompt_name||"Prompt 未记录"} · {r.question}</option>)}</select></label>)}</div>
    {error&&<p role="alert" className="governance-error">{error}</p>}{loading&&<p role="status" className="small-muted">加载对照…</p>}{left&&left===right&&<p className="review-note">请选择两次不同的运行。</p>}{!loading&&runs.length<2&&<p className="empty-small">至少需要两次已完成且可查看的分析。</p>}
    {data&&<div className="review-note"><strong>{data.controlled_pair?"满足同源 Prompt 对照条件，质量仍待人工评审":"历史回答对照：不能作为受控 Prompt 比较"}</strong>{data.reasons.length>0&&<ul>{data.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul>}<p>{data.scope}</p></div>}</section>
    {data&&<div className="comparison-columns"><AnswerSide data={data.left} label="左侧"/><AnswerSide data={data.right} label="右侧"/></div>}</>;
}
