"use client";

import { useEffect,useState } from "react";
import { GitBranch,RefreshCw,Webhook,LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import "./sync-panel.css";

type Delivery={id:string;event:string;action:string;scopes:string[];status:string;attempts:number;message:string;updated_at:string};
type Status={webhook_enabled:boolean;github_repository_bound:boolean;status:string;message:string;sequence:number;last_full_sync_at:string|null;section_times:Record<string,string>;counts:Record<string,number>;deliveries:Delivery[]};
const sections:Record<string,string>={repository:"仓库与分支",issues:"Issues",pulls:"Pull requests",runs:"GitHub Actions",documents:"README 文档"};
const labels:Record<string,string>={idle:"待同步",ready:"已刷新",running:"同步中",failed:"失败",interrupted:"已中断",queued:"排队中",succeeded:"已完成",ignored:"已忽略"};
function time(value:string|null){if(!value)return "尚未记录";return new Date(/[Zz]|[+-]\d{2}:\d{2}$/.test(value)?value:value+"Z").toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});}

export function SyncPanel({repositoryId,syncing,onSync,onReload}:{repositoryId:string;syncing:boolean;onSync:()=>Promise<void>;onReload:()=>Promise<void>}){
  const [status,setStatus]=useState<Status|null>(null);
  const [error,setError]=useState("");
  const [pending,setPending]=useState(false);
  const root=`/repositories/${repositoryId}/sync`;
  useEffect(()=>{
    let alive=true;
    const refresh=()=>api<Status>(`${root}/status`).then(next=>{if(alive){setStatus(next);setError("");}}).catch(e=>{if(alive)setError(e.message);});
    refresh();const timer=setInterval(refresh,3000);
    return ()=>{alive=false;clearInterval(timer);};
  },[root]);
  async function retry(id:string){setPending(true);setError("");try{await api(`${root}/deliveries/${id}/retry`,{method:"POST"});setStatus(await api<Status>(`${root}/status`));}catch(e){setError((e as Error).message);}finally{setPending(false);}}
  async function reload(){setPending(true);try{await onReload();setStatus(await api<Status>(`${root}/status`));}catch(e){setError((e as Error).message);}finally{setPending(false);}}
  return <section className="panel settings-panel sync-panel">
    <div className="panel-heading"><div><h3>仓库数据同步</h3><p className="small-muted">手动同步全部快照；事件只刷新受影响的列表，分项时间可能不同。</p></div><GitBranch size={18}/></div>
    {error&&<p className="sync-error" role="alert">{error}</p>}
    {status?<>
      <div className="sync-summary"><span className="tag">{labels[status.status]||status.status}</span><span>{status.message}</span><span className="small-muted">已完成 {status.sequence} 次刷新</span></div>
      <div className="sync-actions"><button className="button primary" disabled={syncing||pending||status.status==="running"} onClick={onSync}>{syncing?<LoaderCircle size={14} className="spin"/>:<RefreshCw size={14}/>}手动同步全部快照</button><button className="button secondary" disabled={pending||syncing} onClick={reload}>读取最新看板数据</button></div>
      <p className="small-muted">最近完整同步：{time(status.last_full_sync_at)}。列表范围仍为最多 100 条开放 Issues、100 个开放 PR、30 次 Actions。</p>
      <div className="sync-sections">{Object.entries(sections).map(([key,label])=><div key={key}><strong>{label}</strong><time>{time(status.section_times[key]||null)}</time></div>)}</div>
      <div className="sync-webhook"><Webhook size={17}/><div><strong>{status.webhook_enabled?"Webhook 接收已配置":"Webhook 尚未启用"}</strong><p>{status.webhook_enabled?"签名验证后进入后台队列，由独立 Worker 读取 GitHub 当前状态。":"目前可以手动同步。自动更新需配置独立密钥、仓库白名单和 GitHub 可访问的 HTTPS 接收地址。"}</p>{!status.github_repository_bound&&<p>先手动同步一次，记录 GitHub 仓库 ID，再启用事件接收。</p>}</div></div>
      <div className="sync-summary"><span>排队 {status.counts.queued||0}</span><span>处理中 {status.counts.running||0}</span><span>完成 {status.counts.succeeded||0}</span><span>失败 {status.counts.failed||0}</span><span>忽略 {status.counts.ignored||0}</span></div>
      {status.deliveries.length?<div className="sync-deliveries">{status.deliveries.map(item=><article key={item.id}><div><strong>{item.event}{item.action?` · ${item.action}`:""}</strong><span className="tag">{labels[item.status]||item.status}</span></div><p>{item.message} · 尝试 {item.attempts} / 3 次</p><p className="small-muted">{item.scopes.map(scope=>sections[scope]||scope).join(" / ")||"无数据刷新"} · {time(item.updated_at)}</p><code>{item.id}</code>{item.status==="failed"&&item.attempts<3&&status.webhook_enabled&&<button className="button secondary" disabled={pending} onClick={()=>retry(item.id)}>重试只读刷新</button>}</article>)}</div>:<p className="empty-small">尚无已验证的事件投递；当前界面没有接入真实 GitHub Webhook。</p>}
    </>:<p className="small-muted">正在读取同步状态…</p>}
  </section>;
}
