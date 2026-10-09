"use client";
import {useEffect,useRef,useState} from "react";
import {Download,FolderPlus,RefreshCw} from "lucide-react";
import {api,Evidence} from "@/lib/api";
import "./answer-evaluation-sets.css";

type Summary={id:string;name:string;note:string;fingerprint:string;pair_count:number;unique_inputs:number;created_by:string;created_at:string};
type Experiment={id:string;question:string;task:string;answers_available:boolean;left:{status:string};right:{status:string}};
type Labels={supported:number;conflict:number;insufficient:number;not_applicable:number};
type Coverage={fields:number;reviewed:number;unreviewed:number;counts:Labels;review_version:number|null;stale_review:boolean};
type Side={run_id:string;snapshot:{question:string;items:{id:string;kind:string;text:string}[];evidence:(Evidence&{path?:string})[]};context:{prompt_name:string;prompt_id:string;model_parameters:{model:string}}};
type Pair={experiment_id:string;eligible:boolean;reasons:string[];comparison:{left:Side;right:Side};coverage:{left:Coverage;right:Coverage}};
type Detail=Summary&{scope:string;pairs:Pair[];coverage:{eligible_pairs:number;excluded_pairs:number;fully_reviewed_pairs:number;excluded_fields:number;fields:number;reviewed:number;unreviewed:number;
  by_prompt:{id:string;prompt_name:string;role:string;model_parameters:{model:string;temperature:number;max_tokens:number};answers:number;fields:number;reviewed:number;unreviewed:number;counts:Labels}[]}};

function FrozenAnswer({side}:{side:Side}) {
  return <section className="evaluation-frozen-answer"><h4>{side.context.prompt_name} · {side.run_id.slice(0,8)}</h4>
    {side.snapshot.items.map(item=><div key={item.id}><strong>{item.id}</strong><p>{item.text}</p></div>)}
    <details><summary>固定证据（{side.snapshot.evidence.length} 项）</summary>{side.snapshot.evidence.map(e=><details key={e.id}><summary>{e.citation||e.path||e.title||e.id}</summary><p className="small-muted">{e.id} · {e.sha||"无源码提交"}</p><pre>{e.content}</pre></details>)}</details>
  </section>;
}

export function AnswerEvaluationSetsPanel({repositoryId,canCreate,onReview}:{repositoryId:string;canCreate:boolean;onReview:(runId:string)=>void}) {
  const root=`/repositories/${repositoryId}`;
  const [sets,setSets]=useState<Summary[]>([]),[experiments,setExperiments]=useState<Experiment[]>([]),[selected,setSelected]=useState("");
  const [chosen,setChosen]=useState<string[]>([]),[name,setName]=useState(""),[note,setNote]=useState("");
  const [detail,setDetail]=useState<Detail|null>(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[detailLoading,setDetailLoading]=useState(false);
  const [error,setError]=useState(""),[notice,setNotice]=useState(""),[revision,setRevision]=useState(0);
  const pending=useRef<{payload:string;requestId:string}|null>(null);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    void Promise.all([api<{sets:Summary[]}>(root+"/answer-evaluation-sets",{signal:controller.signal}),api<{records:Experiment[]}>(root+"/prompt-experiments",{signal:controller.signal})])
      .then(([s,e])=>{if(controller.signal.aborted)return;setSets(s.sets);setExperiments(e.records);setSelected(previous=>s.sets.some(value=>value.id===previous)?previous:s.sets[0]?.id||"");})
      .catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[root,revision]);
  useEffect(()=>{
    const controller=new AbortController();setDetail(null);setError("");setDetailLoading(false);
    if(selected){setDetailLoading(true);void api<Detail>(root+`/answer-evaluation-sets/${selected}`,{signal:controller.signal})
      .then(value=>{if(!controller.signal.aborted)setDetail(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);})
      .finally(()=>{if(!controller.signal.aborted)setDetailLoading(false);});}
    return()=>controller.abort();
  },[root,selected,revision]);
  const create=async()=>{
    if(busy)return;
    setError("");setNotice("");
    if(!name.trim()||!chosen.length){setError("填写样本集名称，并选择至少一组已完成对照。");return;}
    const payload={name:name.trim(),note:note.trim(),experiment_ids:[...chosen].sort()};
    const serialized=JSON.stringify(payload);
    if(pending.current?.payload!==serialized)pending.current={payload:serialized,requestId:crypto.randomUUID().replaceAll("-","")};
    setBusy(true);
    try{
      const saved=await api<Summary>(root+"/answer-evaluation-sets",{method:"POST",body:JSON.stringify({...payload,request_id:pending.current.requestId})});
      pending.current=null;setSelected(saved.id);setChosen([]);setName("");setNote("");setRevision(value=>value+1);
      setNotice("固定样本集已保存。没有新增模型请求或人工标注。");
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const available=experiments.filter(e=>e.answers_available);
  return <div className="evaluation-sets-layout">
    <section className="panel governance-card"><div className="panel-heading"><h3><FolderPlus size={18}/>评估样本集</h3><button className="button secondary" disabled={busy||loading||detailLoading} onClick={()=>setRevision(value=>value+1)}><RefreshCw size={14}/>刷新标注进度</button></div>
      <p>把已完成的真实对照保存为固定样本，逐步整理人工评审数据。保存、查看和导出沿用已有回答，不会调用模型。</p>
      {canCreate&&<fieldset disabled={busy||loading} className="governance-form evaluation-set-form"><label>样本集名称<input aria-label="评估样本集名称" maxLength={80} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：源码参数解释评估"/></label>
        <label>选择说明<textarea aria-label="评估样本集说明" maxLength={1000} value={note} onChange={e=>setNote(e.target.value)} placeholder="说明这些问题覆盖的场景与尚缺的样本。"/></label>
        <fieldset className="evaluation-set-choices"><legend>最近 20 组中的已完成对照 · 最多选择 20 组</legend>{available.map(e=><label key={e.id}><input type="checkbox" aria-label={`选择对照组 ${e.id.slice(0,8)}`} checked={chosen.includes(e.id)} onChange={event=>setChosen(previous=>event.target.checked?[...previous,e.id]:previous.filter(id=>id!==e.id))}/><span>{e.question} <small>{e.task} · {e.id.slice(0,8)}</small></span></label>)}{!loading&&!available.length&&<p className="empty-small">尚无已完成的两版对照。先在“回答对照”完成一组分析。</p>}</fieldset>
        <button className="button primary" disabled={!chosen.length||!name.trim()} onClick={()=>void create()}>{busy?"保存中…":"保存为固定样本集"}</button><p className="small-muted">服务端会再次核对实际同源条件。网络失败后重试复用本页的提交编号；重新加载后请先查看已保存列表。</p>
      </fieldset>}
      <label className="delivery-select">已保存样本集<select aria-label="选择评估样本集" value={selected} disabled={busy||loading||detailLoading} onChange={e=>setSelected(e.target.value)}>{!sets.length&&<option value="">暂无样本集</option>}{sets.map(s=><option key={s.id} value={s.id}>{s.name} · {s.pair_count} 组 · {s.id.slice(0,8)}</option>)}</select></label>
    </section>
    {error&&<p role="alert" className="governance-error">{error}</p>}{notice&&<p role="status" className="review-note">{notice}</p>}{(loading||detailLoading)&&<p role="status" className="small-muted">加载评估样本…</p>}
    {detail&&<><section className="panel governance-card"><div className="panel-heading"><h3>{detail.name}</h3><a className="text-button" href={`/api${root}/answer-evaluation-sets/${detail.id}/export`}><Download size={14}/>导出固定样本与当前标注</a></div>
      {detail.note&&<p className="memory-content">{detail.note}</p>}<p className="small-muted">{detail.pair_count} 组对照 · {detail.unique_inputs} 份不同模型输入 · 样本版本 {detail.fingerprint.slice(0,12)} · {detail.created_by}</p>
      <div className="delivery-stats"><div><strong>{detail.coverage.eligible_pairs}</strong><span>来源仍一致的对照</span></div><div><strong>{detail.coverage.reviewed} / {detail.coverage.fields}</strong><span>有效人工标注 / 可评审字段</span></div><div><strong>{detail.coverage.fully_reviewed_pairs}</strong><span>两侧字段均已标注的组</span></div></div>
      <p className="review-note">{detail.scope} 退出当前统计 {detail.coverage.excluded_pairs} 组 / {detail.coverage.excluded_fields} 个字段，固定原文仍保留。样本数量不代表场景覆盖充分。</p>
      <div className="delivery-table-scroll"><table className="delivery-table"><thead><tr><th>Prompt / 模型</th><th>标注 / 字段</th><th>支持</th><th>冲突</th><th>不足</th><th>不作判断</th></tr></thead><tbody>{detail.coverage.by_prompt.map(g=><tr key={g.id}><td>{g.prompt_name}<br/><small>{g.model_parameters.model} · {g.role} · 温度 {g.model_parameters.temperature} · 上限 {g.model_parameters.max_tokens}</small></td><td>{g.reviewed} / {g.fields}</td><td>{g.counts.supported}</td><td>{g.counts.conflict}</td><td>{g.counts.insufficient}</td><td>{g.counts.not_applicable}</td></tr>)}</tbody></table></div>
    </section>
    {detail.pairs.map(pair=><section className="panel governance-card" key={pair.experiment_id}><div className="panel-heading"><h3>{pair.comparison.left.snapshot.question}</h3><span className="tag">{pair.eligible?"来源一致":"已退出当前统计"}</span></div>
      {pair.reasons.length>0&&<ul className="review-note">{pair.reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul>}
      <div className="evaluation-set-review-actions">{(["left","right"] as const).map(side=>{const value=pair.comparison[side],progress=pair.coverage[side];return <div key={side}><strong>{value.context.prompt_name}</strong><p className="small-muted">{progress.reviewed} / {progress.fields} 已标注{progress.review_version?` · v${progress.review_version}`:""}{progress.stale_review?" · 旧评审已过期":""}</p><button className="button secondary" onClick={()=>onReview(value.run_id)}>{pair.eligible?"查看与评审回答":"查看当前回答"}</button></div>;})}</div>
      <details className="evaluation-set-frozen"><summary>查看保存时的原回答与固定证据</summary><div className="evaluation-set-frozen-columns"><FrozenAnswer side={pair.comparison.left}/><FrozenAnswer side={pair.comparison.right}/></div></details>
    </section>)}</>}
  </div>;
}
