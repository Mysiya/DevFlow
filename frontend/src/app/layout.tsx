import type { Metadata, Viewport } from "next";
import { MobileAppProvider } from "@/components/mobile-app-provider";
import "./globals.css";
import "./mobile.css";

export const metadata: Metadata = {
  title: "DevFlow AI · 研发协作工作台",
  description: "以证据为基础的 Issue 分诊、PR 审查与 CI 排障工作台",
  applicationName:"DevFlow AI",
  manifest:"/manifest.webmanifest",
  appleWebApp:{capable:true,title:"DevFlow",statusBarStyle:"default"},
  icons:{icon:[{url:"/icons/icon-192.png",sizes:"192x192",type:"image/png"}],apple:[{url:"/icons/apple-touch-icon.png",sizes:"180x180",type:"image/png"}]},
};

export const viewport:Viewport={width:"device-width",initialScale:1,viewportFit:"cover",themeColor:"#172724"};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><MobileAppProvider>{children}</MobileAppProvider></body>
    </html>
  );
}
