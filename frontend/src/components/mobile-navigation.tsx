"use client";
import { useEffect, useRef } from "react";
import { Activity, Bot, BookOpen, History, Menu, Code2, ShieldCheck, FlaskConical, Settings2, X, GitBranch } from "lucide-react";
import type { Repository } from "@/lib/api";

type Page = "overview"|"workspace"|"code"|"knowledge"|"history"|"settings"|"governance"|"delivery";
export function MobileNavigation({page,onNavigate,repositories,repositoryId,onRepositoryChange,disabled}: {
  page:Page;onNavigate:(page:Page)=>void;repositories:Repository[];repositoryId:string;
  onRepositoryChange:(id:string)=>void;disabled:boolean;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const primary=[{id:"overview",label:"概览",icon:Activity},{id:"workspace",label:"工作台",icon:Bot},
    {id:"knowledge",label:"知识",icon:BookOpen},{id:"history",label:"记录",icon:History}] as const;
  const extra=[{id:"code",label:"代码工作区",icon:Code2},{id:"governance",label:"记忆与审批",icon:ShieldCheck},
    {id:"delivery",label:"评测与交付",icon:FlaskConical},{id:"settings",label:"设置与安装",icon:Settings2}] as const;
  const more=extra.some(item=>item.id===page);
  useEffect(()=>{
    const media=window.matchMedia("(max-width: 760px)");
    const close=()=>{if(!media.matches)dialog.current?.close();};
    media.addEventListener("change",close);return()=>media.removeEventListener("change",close);
  },[]);
  function navigate(next:Page) {dialog.current?.close();onNavigate(next);window.scrollTo({top:0,behavior:"instant"});}
  return <>
    <nav className="mobile-bottom-nav" aria-label="手机主导航">
      {primary.map(item=><button key={item.id} aria-current={page===item.id?"page":undefined} className={page===item.id?"active":""} onClick={()=>navigate(item.id)}>
        <item.icon size={21}/><span>{item.label}</span>
      </button>)}
      <button aria-label="更多功能与仓库选择" aria-haspopup="dialog" className={more?"active":""} onClick={()=>dialog.current?.showModal()}><Menu size={21}/><span>更多</span></button>
    </nav>
    <dialog ref={dialog} className="mobile-more-sheet" aria-labelledby="mobile-more-title" onClick={event=>{if(event.target===dialog.current)dialog.current.close();}}>
      <div className="mobile-sheet-heading"><h2 id="mobile-more-title">你的工作空间</h2><button type="button" aria-label="关闭更多功能" onClick={()=>dialog.current?.close()}><X size={22}/></button></div>
      <label className="mobile-repository-label"><GitBranch size={18}/>当前仓库
        <select aria-label="手机选择仓库" value={repositoryId} disabled={disabled} onChange={e=>onRepositoryChange(e.target.value)}>
          {repositories.length?repositories.map(repo=><option key={repo.id} value={repo.id}>{repo.full_name}</option>):<option value="">尚未连接仓库</option>}
        </select>
      </label>
      <div className="mobile-more-grid">{extra.map(item=><button key={item.id} aria-current={page===item.id?"page":undefined} onClick={()=>navigate(item.id)}><item.icon size={22}/><span>{item.label}</span></button>)}</div>
      <p>分析在服务器运行。关闭手机页面后，可以在“记录”里继续查看。</p>
    </dialog>
  </>;
}
