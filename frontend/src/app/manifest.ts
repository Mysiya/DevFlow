import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id:"/",name:"DevFlow AI",short_name:"DevFlow",lang:"zh-CN",
    description:"查看源码、发起研发分析并跟踪后台任务",
    start_url:"/",scope:"/",display:"standalone",background_color:"#f5f7f5",theme_color:"#172724",
    icons:[
      {src:"/icons/icon-192.png",sizes:"192x192",type:"image/png",purpose:"any"},
      {src:"/icons/icon-512.png",sizes:"512x512",type:"image/png",purpose:"any"},
      {src:"/icons/icon-512.png",sizes:"512x512",type:"image/png",purpose:"maskable"},
    ],
  };
}
