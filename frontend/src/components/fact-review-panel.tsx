"use client";
import {useEffect,useRef,useState} from "react";
import {SearchCheck,TriangleAlert} from "lucide-react";
import {Analysis,api} from "@/lib/api";
import "./fact-review.css";

const labels = {matched:"数值与声明一致",conflict:"源码数值冲突",insufficient:"依据不足 · 未验证"};
const fields:Record<string,string> = {title:"标题",summary:"摘要",recommendation:"分析建议",findings:"发现",next_steps:"下一步",gaps:"待确认项"};

export function FactReviewPanel({analysis,repositoryId,runId,onReview}:{analysis:Analysis;repositoryId:string;runId:string;onReview:(value:Analysis)=>void}){
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const controller=useRef<AbortController|null>(null);
  useEffect(()=>()=>controller.current?.abort(),[]);
  const review=analysis.fact_review;
  if(!review&&!analysis.evidence.some(e=>e.id.startsWith("code:")))return null;
  const recheck=async()=>{
    if(busy)return;
    setBusy(true);setError("");controller.current=new AbortController();
    try{
      const result=await api<{run_id:string;persisted:false;reviewed_analysis:Analysis}>(`/repositories/${repositoryId}/runs/${runId}/fact-review`,{signal:controller.current.signal});
      if(!controller.current.signal.aborted)onReview(result.reviewed_analysis);
    }catch(e){if(!controller.current.signal.aborted)setError((e as Error).message);}
    finally{if(!controller.current.signal.aborted)setBusy(false);}
  };
  return <section className={`fact-review ${review?.status==="conflict"?"fact-conflict":""}`} aria-label="源码事实核对">
    <div className="fact-heading"><h3>{review?.status==="conflict"?<TriangleAlert size={18}/>:<SearchCheck size={18}/>}源码事实核对</h3>
      <button className="text-button" disabled={busy||!runId} onClick={recheck}>{busy?"核对中…":review?"重新核对":"核对源码事实"}</button></div>
    {error&&<p role="alert" className="governance-error">{error}</p>}
    {!review?<p>此历史回答尚未做数值核对。可以按它引用的固定源码版本生成复核预览。</p>:<>
      <p className="fact-status">{review.status==="conflict"?`发现 ${review.counts.conflict} 项源码事实冲突，冲突段落已移出正文，暂停自动生成评论草稿。`:review.status==="unavailable"?"当前片段没有可识别的数值或公式来源。":review.categories?"已完成有限数值与公式关系核对，其余解释仍需人工检查。":"此历史记录只做过数值核对，可重新核对公式关系；其余解释仍需人工检查。"}</p>
      <p>{review.scope}</p>
      {review.categories?<div className="fact-categories">{([{id:"numeric",label:"数值声明"},{id:"bm25_relation",label:"公式关系"}] as const).map(category=><div className="fact-counts" key={category.id}><strong>{category.label}</strong><span>一致 {review.categories![category.id].matched}</span><span>冲突 {review.categories![category.id].conflict}</span><span>未验证 {review.categories![category.id].insufficient}</span></div>)}</div>:<div className="fact-counts"><span>一致 {review.counts.matched}</span><span>冲突 {review.counts.conflict}</span><span>未验证 {review.counts.insufficient}</span></div>}
      <p className="small-muted">计数仅含识别出的有限数值和关系句式，未识别的写法和其余解释不计入。</p>
      {review.preview&&<p className="small-muted">这是复核预览，历史分析与执行记录保留原样。</p>}
      {review.checks.length===0&&<p className="small-muted">未找到支持的数值或关系表述；不能据此判断回答正确。</p>}
      <div className="fact-checks">{review.checks.map((check,index)=><details key={index} open={check.status==="conflict"}>
        <summary><span className={`fact-badge ${check.status}`}>{check.kind==="bm25_relation"?(check.status==="matched"?"数学关系一致":check.status==="conflict"?"公式关系冲突":labels.insufficient):labels[check.status]}</span><strong>{check.claim}</strong></summary>
        <p>{check.task_title?`${check.task_title} · `:""}{fields[check.field.split(".")[0]]||check.field}：{check.reason}</p>
        {check.expected.map((fact,i)=><p className="fact-source" key={i}><a href={`#evidence-${fact.evidence_id.replace(/:/g,"-")}`}>{fact.statement||`${fact.name} = ${fact.value}`} · {fact.path} L{fact.line}</a><span>{fact.subject} · SHA {fact.sha.slice(0,12)}</span></p>)}
        <div className="fact-original"><span>原表述</span><p>{check.original_text}</p></div>
      </details>)}</div>
      {review.original_analysis&&<details className="fact-original-analysis"><summary>查看核对前的原回答</summary><h4>{review.original_analysis.title}</h4><p>{review.original_analysis.summary}</p><p>原建议：{review.original_analysis.recommendation}</p>{review.original_analysis.findings.map((f,i)=><p key={i}>{f.title}：{f.detail}</p>)}{[...review.original_analysis.next_steps,...review.original_analysis.gaps].map((line,i)=><p key={i}>{line}</p>)}</details>}
      {review.limited&&<p className="small-muted">达到检查数量上限，超出部分未验证。</p>}
      <p className="small-muted">检查版本 {review.checker_revision.slice(0,12)} · 来源版本 {review.source_hash.slice(0,12)}</p>
    </>}
  </section>;
}
