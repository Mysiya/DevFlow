"use client";
import {useEffect, useState} from "react";
import {Download, ClipboardCheck, Save, RefreshCw} from "lucide-react";
import {api, Evidence, FactReview} from "@/lib/api";
import "./answer-review.css";

type Decision = "supported" | "conflict" | "insufficient" | "not_applicable";
type Annotation = {item_id:string; decision:Decision; evidence_ids:string[]; note:string};
type Item = {id:string; kind:string; text:string; evidence_ids:string[]};
type Snapshot = {question:string; run_id:string; mode:string; provider:string; items:Item[]; evidence:(Evidence & {path?:string;line_start?:number;line_end?:number})[]};
type Record = {id:string; version:number; snapshot_hash:string; annotations:Annotation[]; note:string; created_by:string; created_at:string};
type Detail = {snapshot:Snapshot; snapshot_hash:string; latest_version:number; current_review:Record|null; records:Record[]; automatic_review:FactReview; scope:string};
type History = Record & {snapshot:Snapshot; automatic_review:FactReview};
type Status = {scope:string; counts:{supported:number;conflict:number;insufficient:number;not_applicable:number;unreviewed:number}; stale_reviews:number; excluded_runs:number; runs:{id:string;question:string;mode:string;provider:string;reviewed_count:number;item_count:number;stale:boolean;reviewable:boolean}[]};
const decisions:{id:Decision; label:string}[]=[{id:"supported",label:"有证据支持"},{id:"conflict",label:"与证据冲突"},{id:"insufficient",label:"证据不足"},{id:"not_applicable",label:"不作事实判断"}];
const kindNames:{[key:string]:string}={title:"回答标题",summary:"摘要",recommendation:"建议",finding:"发现",next_steps:"下一步",gaps:"待确认项"};

export function AnswerReviewPanel({repositoryId,canReview,initialRunId=""}:{repositoryId:string;canReview:boolean;initialRunId?:string}) {
  const root=`/repositories/${repositoryId}`;
  const [status,setStatus]=useState<Status|null>(null),[runId,setRunId]=useState(""),[detail,setDetail]=useState<Detail|null>(null);
  const [historyId,setHistoryId]=useState(""),[history,setHistory]=useState<History|null>(null);
  const [annotations,setAnnotations]=useState<{[key:string]:Annotation}>({}),[note,setNote]=useState("");
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const initialize=(data:Detail)=>{setDetail(data);setAnnotations(Object.fromEntries((data.current_review?.annotations||[]).map(a=>[a.item_id,a])));setNote(data.current_review?.note||"");};
  useEffect(()=>{
    const controller=new AbortController();
    void api<Status>(root+"/answer-reviews/status",{signal:controller.signal}).then(data=>{if(controller.signal.aborted)return;setStatus(data);setRunId(initialRunId||data.runs.find(r=>r.reviewable)?.id||"");}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return ()=>controller.abort();
  },[root,initialRunId]);
  useEffect(()=>{
    setDetail(null);setHistoryId("");setHistory(null);setError("");setNotice("");
    if(!runId){setLoading(false);return;}
    const controller=new AbortController();setLoading(true);
    void api<Detail>(root+`/runs/${runId}/answer-reviews`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)initialize(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[root,runId]);
  useEffect(()=>{
    setHistory(null);if(!historyId)return;
    const controller=new AbortController();setLoading(true);
    void api<History>(root+`/runs/${runId}/answer-reviews/${historyId}`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setHistory(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[root,runId,historyId]);
  const refresh=async()=>{
    setBusy(true);setError("");setNotice("");
    try {const s=await api<Status>(root+"/answer-reviews/status");setStatus(s);if(runId)initialize(await api<Detail>(root+`/runs/${runId}/answer-reviews`));setHistoryId("");setHistory(null);}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const save=async()=>{
    if(!detail||busy)return;
    const selected=Object.values(annotations);setError("");setNotice("");
    if(!selected.length){setError("请至少标注一个字段。");return;}
    if(selected.some(a=>!a.note.trim())){setError("每项标注都需要填写理由。");return;}
    if(selected.some(a=>(a.decision==="supported"||a.decision==="conflict")&&!a.evidence_ids.length)){setError("有证据支持或冲突的标注，须勾选至少一项证据。");return;}
    setBusy(true);
    try{
      await api(root+`/runs/${runId}/answer-reviews`,{method:"POST",body:JSON.stringify({snapshot_hash:detail.snapshot_hash,expected_version:detail.latest_version,annotations:selected,note})});
      initialize(await api<Detail>(root+`/runs/${runId}/answer-reviews`));setStatus(await api<Status>(root+"/answer-reviews/status"));setNotice("评审已保存为新版本，原回答和旧评审保留。");
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const snapshot=history?.snapshot||detail?.snapshot,automatic=history?.automatic_review||detail?.automatic_review;
  const visibleAnnotations=history?Object.fromEntries(history.annotations.map(a=>[a.item_id,a])):annotations;
  const readonly=!!historyId||!canReview;
  const update=(item:Item,patch:Partial<Annotation>)=>setAnnotations(previous=>({...previous,[item.id]:{...(previous[item.id]||{item_id:item.id,decision:"insufficient",evidence_ids:[],note:""}),...patch}}));
  return <div className="answer-review-layout">
    <section className="panel governance-card"><div className="panel-heading"><h3><ClipboardCheck size={18}/>真实回答评审</h3><button className="button secondary" disabled={busy||loading} onClick={()=>void refresh()}><RefreshCw size={14}/>重新加载</button></div>
      <p>核对已保存回答的原文和当时证据，逐项填写判断与理由。未标注项不计为正确；自动检查只覆盖有限数值和公式关系。</p>
      {status&&<><div className="delivery-stats review-counts"><div><strong>{status.counts.unreviewed}</strong><span>待评审字段</span></div><div><strong>{status.counts.supported}</strong><span>标注为有证据支持</span></div><div><strong>{status.counts.conflict}</strong><span>标注为与证据冲突</span></div><div><strong>{status.counts.insufficient}</strong><span>标注为证据不足</span></div></div><p className="small-muted">{status.scope} 不作事实判断 {status.counts.not_applicable} 项；过期评审 {status.stale_reviews} 条；超出范围 {status.excluded_runs} 次。</p></>}
      <a className="text-button" href={`/api${root}/answer-reviews/export`}><Download size={14}/>导出回答与标注 JSON</a>
      <fieldset disabled={busy||loading} className="answer-review-controls"><label className="delivery-select">来源分析<select aria-label="选择待评审分析" value={runId} onChange={e=>setRunId(e.target.value)}>{runId&&!status?.runs.some(r=>r.id===runId)&&<option value={runId}>固定样本来源 · {runId.slice(0,12)}{detail&&` · ${detail.snapshot.question.slice(0,52)}`}</option>}{!runId&&!status?.runs.length&&<option value="">暂无已完成分析</option>}{status?.runs.map(r=><option key={r.id} value={r.id} disabled={!r.reviewable}>{r.question.slice(0,52)} · {r.mode} · {r.reviewed_count}/{r.item_count} · {r.id.slice(0,8)}{r.stale?" · 评审过期":""}</option>)}</select></label>
        {detail&&<label className="delivery-select">评审版本<select aria-label="选择回答评审版本" value={historyId} onChange={e=>setHistoryId(e.target.value)}><option value="">当前回答 · {detail.current_review?`评审 v${detail.current_review.version}`:"尚未标注"}</option>{detail.records.map(r=><option key={r.id} value={r.id}>历史 v{r.version} · {r.created_by} · {r.annotations.length} 项</option>)}</select></label>}
      </fieldset>
    </section>
    {error&&<p role="alert" className="governance-error">{error}</p>}{notice&&<p role="status" className="review-note">{notice}</p>}{loading&&<p role="status" className="review-note">正在加载回答和证据…</p>}
    {snapshot&&!loading&&<>
      <section className="panel governance-card"><h3>{history?`历史评审 v${history.version}`:"待核对的原回答"}</h3><p className="memory-content">{snapshot.question}</p><p className="small-muted">来源 {snapshot.run_id.slice(0,12)} · {snapshot.mode} / {snapshot.provider} · 回答版本 {(history?.snapshot_hash||detail?.snapshot_hash||"").slice(0,12)}</p>
        {!!historyId&&<p className="review-note">历史版本只读，展示保存评审时的回答和证据。</p>}
        {!historyId&&detail?.latest_version&&!detail.current_review?<p className="review-note">原回答发生变化，旧标注已过期。请基于当前证据重新评审。</p>:null}
        {automatic?.counts?<p className="review-note">有限自动核对：一致 {automatic.counts.matched}、冲突 {automatic.counts.conflict}、未验证 {automatic.counts.insufficient}。这些计数不是整段回答的质量分数，人工标注不会改变自动判断或草稿审批。</p>:<p className="review-note">此回答没有可识别的源码自动核对项，请依据所列证据人工评审。</p>}
      </section>
      <section className="panel governance-card"><h3>证据原文 · {snapshot.evidence.length} 项</h3><div className="delivery-cases">{snapshot.evidence.map((e,index)=><details id={`review-evidence-${index}`} key={e.id}><summary>{e.citation||e.path||e.title||e.id}{e.sha&&` · SHA ${e.sha.slice(0,12)}`}</summary><p className="small-muted">{e.id}</p><pre className="delivery-report">{e.content}</pre></details>)}</div></section>
      <fieldset className="answer-review-fields" disabled={readonly||busy}>
        {snapshot.items.map((item,index)=>{const a=visibleAnnotations[item.id];return <article className="panel governance-card" key={item.id}><div className="panel-heading"><h3>{kindNames[item.kind]||item.kind} {index+1}</h3><span className="tag">{item.id}</span></div><p className="memory-content review-item-text">{item.text}</p>
          {item.evidence_ids.length>0&&<p className="small-muted">原回答引用：{item.evidence_ids.map(id=>{const i=snapshot.evidence.findIndex(e=>e.id===id);return i>=0?<a key={id} href={`#review-evidence-${i}`}>证据 {i+1} </a>:<span key={id}>无效引用 {id} </span>;})}</p>}
          <div className="governance-form"><label>判断<select aria-label={`${item.id} 判断`} value={a?.decision||""} onChange={e=>{if(!e.target.value)setAnnotations(previous=>{const next={...previous};delete next[item.id];return next;});else update(item,{decision:e.target.value as Decision});}}><option value="">未标注</option>{decisions.map(d=><option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
            {a&&<><label>标注理由<textarea aria-label={`${item.id} 标注理由`} maxLength={1000} value={a.note} onChange={e=>update(item,{note:e.target.value})} placeholder="说明哪句得到支持、哪句冲突，或缺少什么证据。"/></label><fieldset className="review-evidence-options"><legend>用于判断的证据（支持或冲突必选）</legend>{snapshot.evidence.map((e,i)=><label key={e.id}><input aria-label={`${item.id} 证据 ${i+1}`} type="checkbox" checked={a.evidence_ids.includes(e.id)} onChange={event=>update(item,{evidence_ids:event.target.checked?[...a.evidence_ids,e.id]:a.evidence_ids.filter(id=>id!==e.id)})}/><span>证据 {i+1} · {e.citation||e.path||e.title||e.id}</span></label>)}</fieldset></>}
          </div></article>;})}
        <section className="panel governance-card governance-form"><label>评审备注<textarea aria-label="回答评审备注" value={history?.note??note} maxLength={1000} onChange={e=>setNote(e.target.value)}/></label>{!readonly&&<button className="button primary" disabled={busy||!Object.keys(annotations).length} onClick={()=>void save()}><Save size={14}/>{busy?"保存中…":"保存评审版本"}</button>}<p className="small-muted">每次保存包含本轮全部标注，修改后保留旧版本。标注仅用于评测，不会批准记忆、发布评论或改写原回答。</p></section>
      </fieldset>
    </>}
  </div>;
}
