"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Download, Smartphone, WifiOff } from "lucide-react";
import "./mobile-app.css";

type InstallEvent=Event & {prompt:()=>Promise<void>;userChoice:Promise<{outcome:"accepted"|"dismissed"}>};
type AppState={online:boolean;secure:boolean;installed:boolean;nativeAndroid:boolean;canInstall:boolean;ios:boolean;origin:string;loopback:boolean;workerError:string;notice:string;install:()=>Promise<void>};
const AppContext=createContext<AppState>({online:true,secure:false,installed:false,nativeAndroid:false,canInstall:false,ios:false,origin:"",loopback:false,workerError:"",notice:"",install:async()=>{}});
export function useMobileApp() {return useContext(AppContext);}

export function MobileAppProvider({children}:{children:React.ReactNode}) {
  const pending=useRef<InstallEvent|null>(null);
  const [state,setState]=useState<Omit<AppState,"install">>({online:true,secure:false,installed:false,nativeAndroid:false,canInstall:false,ios:false,origin:"",loopback:false,workerError:"",notice:""});
  useEffect(()=>{
    let disposed=false;
    const display=window.matchMedia("(display-mode: standalone)");
    const installed=()=>display.matches||navigator.userAgent.includes("DevFlowAndroid/")||!!(navigator as Navigator & {standalone?:boolean}).standalone;
    setState(value=>({...value,online:navigator.onLine,secure:window.isSecureContext,installed:installed(),nativeAndroid:navigator.userAgent.includes("DevFlowAndroid/"),origin:location.origin,
      loopback:["localhost","127.0.0.1","[::1]"].includes(location.hostname),
      ios:/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1)}));
    const online=()=>setState(value=>({...value,online:true}));
    const offline=()=>setState(value=>({...value,online:false}));
    const mode=()=>setState(value=>({...value,installed:installed()}));
    const prompt=(event:Event)=>{event.preventDefault();pending.current=event as InstallEvent;setState(value=>({...value,canInstall:true}));};
    const completed=()=>{pending.current=null;setState(value=>({...value,installed:true,canInstall:false,notice:"已安装到主屏幕。"}));};
    window.addEventListener("online",online);window.addEventListener("offline",offline);
    window.addEventListener("beforeinstallprompt",prompt);window.addEventListener("appinstalled",completed);display.addEventListener("change",mode);
    if(window.isSecureContext&&"serviceWorker" in navigator) {
      void navigator.serviceWorker.register("/sw.js",{scope:"/",updateViaCache:"none"})
        .catch(()=>{if(!disposed)setState(value=>({...value,workerError:"离线提示暂未准备好，请联网后刷新页面。"}));});
    }
    return()=>{disposed=true;window.removeEventListener("online",online);window.removeEventListener("offline",offline);
      window.removeEventListener("beforeinstallprompt",prompt);window.removeEventListener("appinstalled",completed);display.removeEventListener("change",mode);};
  },[]);
  async function install() {
    const event=pending.current;if(!event)return;
    pending.current=null;setState(value=>({...value,canInstall:false,notice:""}));
    try {
      await event.prompt();const choice=await event.userChoice;
      setState(value=>({...value,notice:choice.outcome==="accepted"?"安装请求已确认，请等待系统完成安装。":"已取消安装。你仍可从浏览器菜单添加到主屏幕。"}));
    } catch {setState(value=>({...value,notice:"暂时无法打开安装窗口，请从浏览器菜单添加到主屏幕。"}));}
  }
  return <AppContext.Provider value={{...state,install}}>
    {!state.online&&<div className="mobile-offline-banner" role="status"><WifiOff size={17}/><span>当前设备已离线。服务器上的任务继续运行，联网后可在“记录”查看。</span></div>}
    {children}
  </AppContext.Provider>;
}

export function MobileInstallPanel() {
  const app=useMobileApp();
  return <section className="panel mobile-install-panel"><div className="panel-heading"><h3><Smartphone size={19}/>手机应用</h3><span className="tag">{app.nativeAndroid?"安卓客户端":app.installed?"已安装":"手机安装"}</span></div>
    <p>在手机上查看源码、发起分析和跟踪后台任务。手机与电脑使用同一服务、同一份记录。</p>
    {!app.nativeAndroid&&!app.ios&&<div className="mobile-apk-download"><a className="button primary" href="/downloads/DevFlow-0.18-debug.apk" download="DevFlow-0.18-debug.apk"><Download size={17}/>下载安卓版 APK</a><p className="small-muted">v0.18 · 安卓 8.0 及以上 · 测试安装包。安装后填写已部署的 HTTPS 服务地址，使用前需要联网。</p></div>}
    {app.installed?<p className="review-note">你正在独立应用窗口中使用 DevFlow。</p>:<>
      {app.canInstall&&app.secure?<button className="button secondary" type="button" onClick={()=>void app.install()}><Download size={17}/>安装手机网页版</button>:
        <p className="review-note">{!app.secure?"当前地址可预览手机版。添加到主屏幕需要可访问的 HTTPS 服务地址。":app.ios?"在 Safari 打开此页面，点“分享”，选择“添加到主屏幕”，再点“添加”。":"在手机浏览器菜单选择“安装应用”或“添加到主屏幕”。浏览器支持时，这里也会显示安装按钮。"}</p>}
    </>}
    {app.origin&&<p className="mobile-service-address">当前服务 <code>{app.origin}</code></p>}
    {app.loopback&&<p className="small-muted">这是这台设备的本地地址。手机需要打开电脑或服务器提供的可访问地址。</p>}
    <p className="small-muted">分析需要联网，计算由服务端完成。离线时显示连接提示，不会保存或自动补发待提交的问题。</p>
    {app.workerError&&<p className="governance-error" role="status">{app.workerError}</p>}{app.notice&&<p className="review-note" role="status">{app.notice}</p>}
  </section>;
}
