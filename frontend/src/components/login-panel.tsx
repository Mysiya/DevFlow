"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { Layers3, LoaderCircle } from "lucide-react";

export function LoginPanel() {
  const [username,setUsername]=useState("");
  const [password,setPassword]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  return <main className="login-page"><section className="panel login-panel">
    <Layers3 size={32}/><p className="eyebrow">DEVFLOW WORKSPACE</p><h1>登录研发工作台</h1>
    <p>使用管理员创建的账号访问获授权的仓库。</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError("");try{await api("/auth/login",{method:"POST",body:JSON.stringify({username,password})});window.location.reload();}catch(err){setError((err as Error).message);setBusy(false);}}}>
      <label>用户名<input aria-label="登录用户名" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required maxLength={40}/></label>
      <label>密码<input aria-label="登录密码" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required maxLength={256}/></label>
      {error&&<p role="alert" className="governance-error">{error}</p>}
      <button className="button primary full-width" disabled={busy}>{busy?<LoaderCircle className="spin" size={16}/>:null}登录</button>
    </form>
  </section></main>;
}
