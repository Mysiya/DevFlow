"use client";
import {useEffect,useRef,useState} from "react";
import {FlaskConical} from "lucide-react";
import {api} from "@/lib/api";

type RunState={id:string;status:string;stop_requested:boolean;message:string|null};
type Experiment={id:string;question:string;task:string;active:boolean;answers_available:boolean;left:RunState;right:RunState;
  sources:{workspace:{sha:string;source:string}|null;document_chunks:number};parameters:{model:string;max_tokens:number};frozen_hash:string;created_by:string};
const statuses:Record<string,string>={queued:"排队中",running:"执行中",completed:"已完成",failed:"失败",interrupted:"中断",cancelled:"已停止"};

export function PromptExperimentPanel({repositoryId,canRun,onReady}:{repositoryId:string;canRun:boolean;onReady:(left:string,right:string)=>void}) {
  const root=`/repositories/${repositoryId}/prompt-experiments`;
  const [records,setRecords]=useState<Experiment[]>([]),[current,setCurrent]=useState<Experiment|null>(null),[selected,setSelected]=useState(""),[enabled,setEnabled]=useState(false),[question,setQuestion]=useState(""),[task,setTask]=useState("knowledge"),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const pending=useRef<{id:string;question:string;task:string}|null>(null);
  useEffect(()=>{const controller=new AbortController();
    void api<{records:Experiment[];enabled:boolean}>(root,{signal:controller.signal}).then(data=>{if(controller.signal.aborted)return;setEnabled(data.enabled);setRecords(data.records);setCurrent(data.records[0]||null);setSelected(data.records[0]?.id||"");}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();},[root]);
  useEffect(()=>{if(!selected)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;let wasActive=!!current?.active;
    const refresh=async()=>{try{const value=await api<Experiment>(root+`/${selected}`,{signal:controller.signal});if(controller.signal.aborted)return;setCurrent(value);setRecords(rows=>rows.map(row=>row.id===value.id?value:row));
      if(value.active){wasActive=true;timer=setTimeout(()=>void refresh(),2000);}else if(wasActive&&value.answers_available){onReady(value.left.id,value.right.id);}
    }catch(e){if(!controller.signal.aborted)setError((e as Error).message);}};
    void refresh();return()=>{controller.abort();if(timer)clearTimeout(timer);};
  },[root,selected,onReady]); // eslint-disable-line react-hooks/exhaustive-deps
  const submit=async()=>{if(busy||current?.active)return;setBusy(true);setError("");const text=question.trim();
    if(!pending.current||pending.current.question!==text||pending.current.task!==task)pending.current={id:crypto.randomUUID().replace(/-/g,""),question:text,task};
    try{const value=await api<Experiment>(root,{method:"POST",body:JSON.stringify({request_id:pending.current.id,question:text,task})});pending.current=null;setRecords(rows=>[value,...rows.filter(r=>r.id!==value.id)].slice(0,20));setCurrent(value);setSelected(value.id);if(value.answers_available)onReady(value.left.id,value.right.id);}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  const stop=async()=>{if(!current||busy)return;setBusy(true);setError("");try{const value=await api<Experiment>(root+`/${current.id}/stop`,{method:"POST"});setCurrent(value);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <section className="panel governance-card"><div className="panel-heading"><h3><FlaskConical size={18}/>一次提交两版对照</h3><span className="tag">基线 v1 / 证据优先 v2</span></div><p>两次分析共用固定的源码、文档、批准记忆和模型参数。提交会调用两次真实模型；失败不自动补发，关闭页面仍在后台执行。</p>
    {!enabled&&<p className="review-note">需要真实模式及模型配置；演示分析不能用来评估 Prompt。</p>}{!canRun&&<p className="small-muted">当前角色可查看记录；编辑者可提交对照。</p>}
    {canRun&&enabled&&<form className="experiment-form" onSubmit={e=>{e.preventDefault();void submit();}}><label className="experiment-question">同一个分析问题<textarea aria-label="两版对照问题" rows={3} maxLength={6000} value={question} onChange={e=>setQuestion(e.target.value)} disabled={busy} placeholder="输入一个可以用当前文档或源码回答的问题…"/></label><div className="experiment-controls"><label>来源<select aria-label="对照来源" value={task} onChange={e=>setTask(e.target.value)} disabled={busy}><option value="knowledge">项目知识</option><option value="code">固定源码</option></select></label><button className="button primary" disabled={busy||!!current?.active||!question.trim()}>{busy?"提交中…":"提交两版分析"}</button></div></form>}
    {error&&<p role="alert" className="governance-error">{error}</p>}
    {records.length>0&&<label className="delivery-select">已提交的对照（最近 20 条）<select aria-label="选择两版对照记录" disabled={busy} value={selected} onChange={e=>{setError("");setCurrent(records.find(r=>r.id===e.target.value)||null);setSelected(e.target.value);}}>{records.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)} · {r.question}</option>)}</select></label>}
    {current&&<div className="experiment-record"><p className="comparison-question">{current.question}</p><div className="experiment-controls">{[{label:"基线 v1",run:current.left},{label:"证据优先 v2",run:current.right}].map(({label,run})=><span className="tag" key={run.id}>{label}：{run.stop_requested&&run.status==="running"?"停止中":statuses[run.status]||run.status}</span>)}
      {current.answers_available&&<button className="button secondary" type="button" onClick={()=>onReady(current.left.id,current.right.id)}>查看这组回答</button>}{canRun&&current.active&&<button className="button secondary" type="button" disabled={busy} onClick={()=>void stop()}>停止这组分析</button>}</div>
      <p className="small-muted">{current.parameters.model} · 每次输出上限 {current.parameters.max_tokens} · 源码 {current.sources.workspace?.sha.slice(0,12)||"未同步"} · 文档片段 {current.sources.document_chunks} · 来源版本 {current.frozen_hash.slice(0,12)}</p>
      {[current.left,current.right].filter(r=>r.message).map(r=><p className="review-note" key={r.id}>{r.id.slice(0,8)}：{r.message}</p>)}{!current.active&&!current.answers_available&&<p className="review-note">这组分析未全部完成，记录已保留；不会自动重试模型。</p>}</div>}
  </section>;
}
