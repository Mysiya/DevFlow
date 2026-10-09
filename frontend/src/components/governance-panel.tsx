"use client";
import { useEffect,useState } from "react";
import { Check, FileText, RefreshCw, ShieldCheck, BookOpen, Users, History, Search } from "lucide-react";
import { api, Analysis, AuditEntry, AuthState, Draft, Governance, ProjectMemory } from "@/lib/api";

const statuses:Record<string,string>={candidate:"待批准",approved:"已批准",rejected:"已拒绝",archived:"已归档",draft:"待提交",pending:"待审核",publishing:"发布中",published:"已发布",uncertain:"发布待核对"};
const roles:Record<string,string>={admin:"管理员",maintainer:"审核者",editor:"编辑者",viewer:"只读成员",none:"无访问权限"};
const actions:Record<string,string>={"run.submit":"提交后台分析","memory.create":"创建候选记忆","memory.edit":"修改记忆","memory.approve":"批准记忆","memory.reject":"拒绝记忆","memory.archive":"归档记忆","draft.create":"创建草稿","draft.edit":"修改草稿","draft.submit":"提交审核","draft.approve":"批准草稿","draft.reject":"拒绝草稿","draft.publish_claim":"领取发布","draft.published":"确认发布","draft.uncertain":"发布待核对","membership.set":"调整仓库权限"};
function time(value:string){return new Date(/[Z]|[+-]\d{2}:\d{2}$/.test(value)?value:value+"Z").toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"});}
type Change=(path:string,body?:object)=>Promise<boolean>;

function MemorySearch({root}:{root:string}){
  const [query,setQuery]=useState(""),[results,setResults]=useState<{id:string;title:string;content:string;citation:string}[]>([]),[searched,setSearched]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  return <section className="panel governance-card"><div className="panel-heading"><h3><Search size={16}/>检索已批准记忆</h3><span className="tag">BM25</span></div><p className="small-muted">只返回当前已批准的版本，候选、拒绝和归档内容不会用于新分析。</p><form className="governance-form" onSubmit={async e=>{e.preventDefault();setBusy(true);setError("");try{const data=await api<{results:typeof results}>(`${root}/memories/search`,{method:"POST",body:JSON.stringify({query})});setResults(data.results);setSearched(true);}catch(err){setError((err as Error).message);}finally{setBusy(false);}}}><label>检索关键词<input aria-label="记忆检索关键词" value={query} onChange={e=>setQuery(e.target.value)} required maxLength={1000}/></label><button className="button secondary" disabled={busy}>{busy?"检索中…":"检索已批准记忆"}</button></form>{error&&<p role="alert" className="governance-error">{error}</p>}{searched&&!results.length&&<p className="empty-small">没有命中的已批准记忆。</p>}{results.map(r=><div className="memory-search-hit" key={r.id}><strong>{r.title}</strong><p className="memory-content">{r.content}</p><p className="small-muted">{r.citation}</p></div>)}</section>;
}

function MemoryCard({memory,permissions,change,root}:{memory:ProjectMemory;permissions:Governance;change:Change;root:string}){
  const [editing,setEditing]=useState(false),[title,setTitle]=useState(memory.title),[content,setContent]=useState(memory.content);
  useEffect(()=>{setTitle(memory.title);setContent(memory.content);setEditing(false);},[memory.version,memory.title,memory.content]);
  return <article className="panel governance-card"><div className="panel-heading"><h3>{memory.title}</h3><span className={`tag review-${memory.status}`}>{statuses[memory.status]}</span></div>
    <p className="small-muted">v{memory.version} · {memory.run_id?`来源运行 ${memory.run_id.slice(0,8)}`:"人工录入"} · {memory.approved_by?`批准人 ${memory.approved_by}`:`创建人 ${memory.author}`}</p>
    {editing?<div className="governance-form"><label>标题<input aria-label="修改记忆标题" value={title} onChange={e=>setTitle(e.target.value)} maxLength={120}/></label><label>内容<textarea aria-label="修改记忆内容" value={content} onChange={e=>setContent(e.target.value)} maxLength={2000}/></label></div>:<p className="memory-content">{memory.content}</p>}
    {!!memory.evidence_ids.length&&<p className="small-muted">附 {memory.evidence_ids.length} 项来源证据</p>}
    <div className="governance-actions">
      {permissions.can_edit&&(editing?<><button className="button primary" onClick={async()=>{if(await change(`${root}/memories/${memory.id}/edit`,{version:memory.version,title,content,run_id:memory.run_id,evidence_ids:memory.evidence_ids}))setEditing(false);}}>保存并重新审核</button><button className="button secondary" onClick={()=>setEditing(false)}>取消编辑</button></>:<button className="button secondary" onClick={()=>setEditing(true)}>编辑</button>)}
      {!editing&&memory.status==="candidate"&&permissions.can_review&&<><button className="button primary" onClick={()=>change(`${root}/memories/${memory.id}/review`,{version:memory.version,decision:"approve"})}><Check size={15}/>批准使用</button><button className="button secondary" onClick={()=>change(`${root}/memories/${memory.id}/review`,{version:memory.version,decision:"reject"})}>拒绝</button></>}
      {!editing&&memory.status!=="archived"&&permissions.can_edit&&<button className="text-button" onClick={()=>change(`${root}/memories/${memory.id}/archive`,{version:memory.version})}>归档</button>}
    </div>
  </article>;
}

function DraftCard({draft,permissions,change,root,repoName}:{draft:Draft;permissions:Governance;change:Change;root:string;repoName:string}){
  const [body,setBody]=useState(draft.body),[kind,setKind]=useState(draft.target_kind||""),[number,setNumber]=useState(String(draft.target_number||"")),[note,setNote]=useState("");
  useEffect(()=>{setBody(draft.body);setKind(draft.target_kind||"");setNumber(String(draft.target_number||""));setNote("");},[draft.version,draft.body,draft.target_kind,draft.target_number]);
  const dirty=body!==draft.body||kind!==(draft.target_kind||"")||number!==String(draft.target_number||"");
  const locked=["publishing","published","uncertain"].includes(draft.status);
  const editable=permissions.can_edit&&!locked;
  const path=`${root}/drafts/${draft.id}`;
  return <article className="panel governance-card"><div className="panel-heading"><h3><FileText size={16}/>草稿 {draft.id.slice(0,8)}</h3><span className={`tag review-${draft.status}`}>{statuses[draft.status]||draft.status}</span></div>
    <p className="small-muted">v{draft.version} · 来源运行 {draft.run_id.slice(0,8)}{draft.approved_by?` · 批准人 ${draft.approved_by}`:""}</p>
    <details className="draft-editor"><summary>查看正文与发布目标</summary><div className="governance-form">
      <label>评论正文<textarea aria-label={`草稿正文 ${draft.id.slice(0,8)}`} value={body} readOnly={!editable} onChange={e=>setBody(e.target.value)} maxLength={30000} rows={10}/></label>
      <div className="governance-fields"><label>目标类型<select aria-label="草稿目标类型" value={kind} disabled={!editable} onChange={e=>{setKind(e.target.value);if(!e.target.value)setNumber("");}}><option value="">仅本地草稿</option><option value="issue">Issue 评论</option><option value="pr">PR 评论</option></select></label><label>目标编号<input aria-label="草稿目标编号" type="number" min={1} disabled={!editable||!kind} value={number} onChange={e=>setNumber(e.target.value)}/></label></div>
      <p className="small-muted">{kind?`${repoName} · ${kind.toUpperCase()} #${number||"待填写"}，需要原分析包含该目标的证据。`:"未绑定 GitHub 目标，批准后仍仅保存在本地。"}</p>
      {draft.expected_sha&&<p className="small-muted">获审提交 {draft.expected_sha.slice(0,12)}</p>}
      {dirty&&<p className="review-note">有未保存修改。保存后原审批失效，需重新审核。</p>}
      {editable&&dirty&&<button className="button secondary" onClick={()=>change(path+"/edit",{version:draft.version,body,target_kind:kind||null,target_number:kind?Number(number):null})}>保存修改</button>}
    </div></details>
    {draft.note&&<p className="review-note">审批说明：{draft.note}</p>}
    <div className="governance-actions">
      {permissions.can_edit&&["draft","rejected"].includes(draft.status)&&<button className="button primary" disabled={dirty} onClick={()=>change(path+"/submit",{version:draft.version})}>提交审核</button>}
      {permissions.can_review&&draft.status==="pending"&&<><input aria-label="草稿审批说明" placeholder="审批说明（可选）" value={note} onChange={e=>setNote(e.target.value)} maxLength={1000}/><button className="button primary" disabled={dirty} onClick={()=>change(path+"/review",{version:draft.version,decision:"approve",note})}>批准草稿</button><button className="button secondary" disabled={dirty} onClick={()=>change(path+"/review",{version:draft.version,decision:"reject",note})}>拒绝</button></>}
      {permissions.can_review&&draft.status==="approved"&&<button className="button primary" disabled={dirty||!permissions.publish_enabled||!draft.target_kind} onClick={()=>{if(window.confirm(`将当前已批准的草稿发布到 ${repoName} ${draft.target_kind?.toUpperCase()} #${draft.target_number} 的评论区？`))void change(path+"/publish",{version:draft.version});}}>发布到 GitHub</button>}
      {permissions.can_review&&draft.status==="uncertain"&&<button className="button secondary" disabled={!permissions.publish_enabled} onClick={()=>change(path+"/reconcile")}>核对发布结果</button>}
      <button className="text-button" onClick={()=>navigator.clipboard.writeText(draft.body)}>复制正文</button>
      {draft.published_url&&<a className="text-button" href={draft.published_url} target="_blank" rel="noreferrer">查看 GitHub 评论</a>}
    </div>
    {draft.publish_message&&<p className="review-note">{draft.publish_message}</p>}
  </article>;
}

type User={id:string;username:string;is_admin:boolean;active:boolean};
type Member={user_id:string;username:string;role:string};
function Members({repo,change}:{repo:string;change:Change}){
  const [users,setUsers]=useState<User[]>([]),[members,setMembers]=useState<Member[]>([]),[username,setUsername]=useState(""),[password,setPassword]=useState(""),[role,setRole]=useState("viewer"),[error,setError]=useState("");
  const refresh=async()=>{try{const [u,m]=await Promise.all([api<User[]>("/admin/users"),api<Member[]>(`/admin/repositories/${repo}/members`)]);setUsers(u);setMembers(m);}catch(err){setError((err as Error).message);}};
  useEffect(()=>{void refresh();},[repo]); // eslint-disable-line react-hooks/exhaustive-deps
  return <section className="panel governance-card"><div className="panel-heading"><h3>用户与仓库权限</h3><Users size={18}/></div><p>只读成员查看资料；编辑者发起分析并提交候选；审核者批准内容和执行已启用的发布。</p>
    <form className="governance-form" onSubmit={async e=>{e.preventDefault();setError("");try{const u=await api<User>("/admin/users",{method:"POST",body:JSON.stringify({username,password})});await api(`/admin/repositories/${repo}/members`,{method:"POST",body:JSON.stringify({user_id:u.id,role})});setUsername("");setPassword("");await refresh();}catch(err){setError((err as Error).message);await refresh();}}}>
      <div className="governance-fields"><label>用户名<input aria-label="新用户名称" value={username} onChange={e=>setUsername(e.target.value)} pattern="[a-zA-Z0-9_.-]{3,40}" required maxLength={40}/></label><label>初始密码<input aria-label="新用户密码" type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)} minLength={12} required maxLength={256}/></label><label>仓库角色<select aria-label="新用户角色" value={role} onChange={e=>setRole(e.target.value)}>{["viewer","editor","maintainer"].map(r=><option key={r} value={r}>{roles[r]}</option>)}</select></label></div><button className="button primary">创建用户并授权</button>
    </form>{error&&<p role="alert" className="governance-error">{error}</p>}
    <div className="member-list">{users.map(u=><div className="member-row" key={u.id}><strong>{u.username}{!u.active?" · 已停用":""}</strong>{u.is_admin?<span className="tag">管理员</span>:<><select aria-label={`${u.username} 的仓库角色`} value={members.find(m=>m.user_id===u.id)?.role||"none"} disabled={!u.active} onChange={async e=>{if(await change(`/admin/repositories/${repo}/members`,{user_id:u.id,role:e.target.value}))await refresh();}}>{["none","viewer","editor","maintainer"].map(r=><option key={r} value={r}>{roles[r]}</option>)}</select>{u.active&&<button className="text-button" onClick={async()=>{if(await change(`/admin/users/${u.id}/disable`))await refresh();}}>停用账号</button>}</>}</div>)}</div>
    <p className="small-muted">本地模式不验证登录身份；启用登录后，仓库角色才会按账号生效。管理员初始账号通过本地环境配置创建。</p>
  </section>;
}

export function GovernancePanel({repositoryId,repoName,auth,sourceRun}:{repositoryId:string;repoName:string;auth:AuthState|null;sourceRun:{id:string;result:Analysis}|null}){
  const root=`/repositories/${repositoryId}`;
  const [tab,setTab]=useState("memories"),[permissions,setPermissions]=useState<Governance|null>(null),[memories,setMemories]=useState<ProjectMemory[]>([]),[drafts,setDrafts]=useState<Draft[]>([]),[entries,setEntries]=useState<AuditEntry[]>([]),[error,setError]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false),[title,setTitle]=useState(""),[content,setContent]=useState(""),[fromRun,setFromRun]=useState(false);
  const refresh=async()=>{const p=await api<Governance>(`${root}/governance`);setPermissions(p);const [m,d,a]=await Promise.all([api<ProjectMemory[]>(`${root}/memories`),api<Draft[]>(`${root}/drafts`),p.can_review?api<AuditEntry[]>(`${root}/audit`):Promise.resolve([])]);setMemories(m);setDrafts(d);setEntries(a);};
  useEffect(()=>{let alive=true;setPermissions(null);setError("");refresh().catch(err=>{if(alive)setError(err.message);});return()=>{alive=false;};},[repositoryId]); // eslint-disable-line react-hooks/exhaustive-deps
  const change:Change=async(path,body)=>{if(busy)return false;setBusy(true);setError("");try{await api(path,{method:"POST",...(body?{body:JSON.stringify(body)}:{})});await refresh();setNotice("已保存，请查看最新状态与审计记录。");return true;}catch(err){setError((err as Error).message);return false;}finally{setBusy(false);}};
  return <div className="governance-layout"><section className="panel governance-intro"><div><p className="eyebrow">HUMAN REVIEW</p><h2>把经验留下，让每次操作有据可查。</h2><p>候选内容先审核。编辑会撤销原审批；记忆在新分析提交时固定版本，归档后不再用于新任务。</p></div><ShieldCheck size={32}/></section>
    <div className="governance-toolbar"><span className="tag">{permissions?roles[permissions.role]:"加载权限…"}</span><span className="small-muted">{auth?.auth_enabled?"登录身份已验证":"本地模式 · 未启用登录"} · GitHub 写回{permissions?.publish_enabled?"已启用":"未启用"}</span><button className="button secondary" onClick={()=>{setError("");void refresh().catch(e=>setError(e.message));}} disabled={busy}><RefreshCw size={14}/>刷新</button></div>
    {error&&<p role="alert" className="governance-error">{error}</p>}{notice&&<p role="status" className="review-note">{notice}</p>}
    <div className="governance-tabs" role="tablist" aria-label="记忆与审批分类">{[{id:"memories",label:`项目记忆 ${memories.length}`,icon:BookOpen},{id:"drafts",label:`草稿审批 ${drafts.length}`,icon:FileText},...(permissions?.can_review?[{id:"audit",label:"操作审计",icon:History}]:[]),...(auth?.user?.is_admin?[{id:"members",label:"成员权限",icon:Users}]:[])].map(t=><button role="tab" aria-selected={tab===t.id} className={tab===t.id?"selected":""} key={t.id} onClick={()=>setTab(t.id)}><t.icon size={15}/>{t.label}</button>)}</div>
    <fieldset className="governance-content" disabled={busy}>{permissions&&tab==="memories"&&<>
      <MemorySearch key={memories.map(m=>`${m.id}:${m.version}`).join(",")} root={root}/>
      {permissions.can_edit&&<section className="panel governance-card"><div className="panel-heading"><h3>创建候选记忆</h3>{sourceRun&&<button className="text-button" onClick={()=>{setTitle(sourceRun.result.title.slice(0,120));setContent(sourceRun.result.summary.slice(0,2000));setFromRun(true);}}>从当前结论提取</button>}</div><form className="governance-form" onSubmit={async e=>{e.preventDefault();if(await change(`${root}/memories`,{title,content,run_id:fromRun&&sourceRun?sourceRun.id:null,evidence_ids:fromRun&&sourceRun?sourceRun.result.evidence.slice(0,16).map(e=>e.id):[]})){setTitle("");setContent("");setFromRun(false);}}}><label>标题<input aria-label="候选记忆标题" value={title} onChange={e=>setTitle(e.target.value)} maxLength={120} required/></label><label>项目约定或可复用经验<textarea aria-label="候选记忆内容" placeholder="例如：API 与 Worker 更改配置后必须一起重启。" value={content} onChange={e=>setContent(e.target.value)} maxLength={2000} required/></label>{fromRun&&sourceRun&&<p className="small-muted">关联运行 {sourceRun.id.slice(0,8)} 及其来源证据</p>}<button className="button primary">保存候选，等待批准</button></form></section>}
      {memories.map(m=><MemoryCard key={m.id} memory={m} permissions={permissions} root={root} change={change}/>)}{!memories.length&&<p className="empty-small">还没有项目记忆。候选记忆获批后，可通过分析工具检索并引用。</p>}
    </>}{permissions&&tab==="drafts"&&<><p className="review-note">发布需要已验证的目标、当前版本审批、登录身份和 GitHub 写权限。发布结果不明确时只能核对，不会自动重发。</p>{drafts.map(d=><DraftCard key={d.id} draft={d} permissions={permissions} root={root} repoName={repoName} change={change}/>)}{!drafts.length&&<p className="empty-small">先在已完成分析中保存评论草稿，再到这里提交审核。</p>}</>}
    {tab==="audit"&&<section className="panel governance-card"><h3>最近 100 条操作</h3>{entries.map(e=><div className="audit-row" key={e.id}><div><strong>{actions[e.action]||e.action}</strong><p>{e.actor} · {e.object_id.slice(0,8)}{e.detail.version?` · v${e.detail.version}`:""}</p>{!!e.detail.note&&<p>{String(e.detail.note)}</p>}</div><time>{time(e.created_at)}</time></div>)}{!entries.length&&<p className="empty-small">暂无操作记录。</p>}</section>}
    {tab==="members"&&<Members repo={repositoryId} change={change}/>}</fieldset>
  </div>;
}
