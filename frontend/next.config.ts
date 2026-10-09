import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return [
      {source:"/sw.js",headers:[{key:"Cache-Control",value:"no-cache, no-store, must-revalidate"},{key:"Service-Worker-Allowed",value:"/"},{key:"Content-Type",value:"application/javascript; charset=utf-8"},{key:"X-Content-Type-Options",value:"nosniff"}]},
      {source:"/api/:path*",headers:[{key:"Cache-Control",value:"no-store"}]},
      {source:"/downloads/DevFlow-0.18-debug.apk",headers:[{key:"Content-Type",value:"application/vnd.android.package-archive"},{key:"Content-Disposition",value:'attachment; filename="DevFlow-0.18-debug.apk"'},{key:"X-Content-Type-Options",value:"nosniff"}]},
      {source:"/downloads/DevFlow-0.18.1-debug.apk",headers:[{key:"Content-Type",value:"application/vnd.android.package-archive"},{key:"Content-Disposition",value:'attachment; filename="DevFlow-0.18.1-debug.apk"'},{key:"X-Content-Type-Options",value:"nosniff"}]},
      {source:"/offline.html",headers:[{key:"Content-Security-Policy",value:"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}]},
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.DEVFLOW_API_URL || "http://127.0.0.1:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
