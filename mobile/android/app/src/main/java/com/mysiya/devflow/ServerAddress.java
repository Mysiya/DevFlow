package com.mysiya.devflow;

import java.net.URI;
import java.util.Locale;

/** HTTPS origin parsing is shared by setup, navigation and authenticated exports. */
public final class ServerAddress {
    private ServerAddress() {}
    /** Move the retired preview endpoint on upgrade; keep user-selected servers. */
    public static String initialServer(String saved, String configured) {
        if (saved == null || saved.isEmpty()) return configured;
        try {
            if (normalize(saved).equals("https://preview-ff00a4ca69754797.up.railway.app/")) return configured;
        } catch (IllegalArgumentException ignored) { }
        return saved;
    }
    private static URI parse(String value) {
        try {
            URI uri = new URI(value.trim());
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getRawUserInfo() != null
                || uri.getPort() == 0 || uri.getPort() > 65535 || value.matches("(?s).*\\p{Cntrl}.*")) throw new IllegalArgumentException();
            return uri;
        } catch (Exception error) { throw new IllegalArgumentException("请输入完整的 HTTPS 服务地址，不包含账号、查询参数或路径。"); }
    }
    public static String normalize(String value) {
        URI uri = parse(value);
        if (uri.getRawQuery() != null || uri.getRawFragment() != null || !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath())))
            throw new IllegalArgumentException("请填写服务根地址，例如 https://devflow.example.com。");
        try { return new URI("https",null,uri.getHost().toLowerCase(Locale.ROOT),uri.getPort()==443?-1:uri.getPort(),"/",null,null).toASCIIString(); }
        catch (Exception error) { throw new IllegalArgumentException("服务地址无效。"); }
    }
    public static boolean sameOrigin(String origin, String destination) {
        try {
            URI a=parse(origin), b=parse(destination);
            return a.getHost().equalsIgnoreCase(b.getHost()) && port(a)==port(b);
        } catch (IllegalArgumentException error) { return false; }
    }
    public static boolean externalHttps(String value) { try {parse(value);return true;} catch(IllegalArgumentException error){return false;} }
    private static int port(URI uri) {return uri.getPort()==-1?443:uri.getPort();}
}
