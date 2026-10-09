package com.mysiya.devflow;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.provider.DocumentsContract;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.*;
import android.widget.*;
import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import java.io.*;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int SAVE_EXPORT=7, MAX_EXPORT=16*1024*1024;
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private LinearLayout root,body;
    private WebView web;
    private ProgressBar progress;
    private String origin="", pendingDownload;
    private int generation=0;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.rgb(245,247,245));setContentView(root);
        root.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30) {
                android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()), keyboard=insets.getInsets(WindowInsets.Type.ime());
                root.setPadding(bars.left,bars.top,bars.right,Math.max(bars.bottom,keyboard.bottom));
            } else root.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return Build.VERSION.SDK_INT>=30?WindowInsets.CONSUMED:insets.consumeSystemWindowInsets();
        });
        LinearLayout toolbar=new LinearLayout(this);toolbar.setGravity(android.view.Gravity.CENTER_VERTICAL);toolbar.setPadding(dp(14),0,dp(6),0);toolbar.setBackgroundColor(Color.rgb(23,39,36));
        TextView name=text("DevFlow AI",18);name.setTextColor(Color.WHITE);toolbar.addView(name,new LinearLayout.LayoutParams(0,dp(48),1));
        Button reload=button("刷新");reload.setTextColor(Color.WHITE);reload.setBackgroundTintList(ColorStateList.valueOf(Color.rgb(44,66,59)));reload.setOnClickListener(v->{if(web!=null)web.reload();else if(!origin.isEmpty())connect(origin);});toolbar.addView(reload);
        Button server=button("连接");server.setTextColor(Color.WHITE);server.setBackgroundTintList(ColorStateList.valueOf(Color.rgb(44,66,59)));server.setOnClickListener(v->switchServer());toolbar.addView(server);root.addView(toolbar);
        progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);progress.setVisibility(View.GONE);root.addView(progress,new LinearLayout.LayoutParams(-1,dp(3)));
        body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);root.addView(body,new LinearLayout.LayoutParams(-1,0,1));
        if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::back);
        String configured=getPreferences(MODE_PRIVATE).getString("server",BuildConfig.DEFAULT_SERVER_URL);
        if(configured.isEmpty())setup("");else connect(configured);
    }
    private int dp(int value){return (int)(value*getResources().getDisplayMetrics().density+.5f);}
    private TextView text(String value,int size){TextView view=new TextView(this);view.setText(value);view.setTextSize(size);view.setTextColor(Color.rgb(24,39,37));view.setGravity(android.view.Gravity.CENTER_VERTICAL);view.setLineSpacing(dp(4),1);return view;}
    private Button button(String value){Button view=new Button(this);view.setText(value);view.setAllCaps(false);view.setMinHeight(dp(48));return view;}
    private void disposeWeb(){if(web!=null){body.removeView(web);web.stopLoading();web.destroy();web=null;}}
    private void setup(String message){
        generation++;disposeWeb();progress.setVisibility(View.GONE);body.removeAllViews();
        ScrollView scroll=new ScrollView(this);LinearLayout panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);panel.setPadding(dp(24),dp(36),dp(24),dp(24));scroll.addView(panel);body.addView(scroll);
        panel.addView(text("连接你的 DevFlow",27));panel.addView(text("填写你部署的 HTTPS 服务地址。登录后，手机与电脑使用同一份任务和记录。",15));
        EditText address=new EditText(this);address.setSingleLine(true);address.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);address.setHint("https://devflow.example.com");address.setText(origin.isEmpty()?getPreferences(MODE_PRIVATE).getString("server",BuildConfig.DEFAULT_SERVER_URL):origin);address.setTextSize(16);panel.addView(address,new LinearLayout.LayoutParams(-1,dp(64)));
        TextView error=text(message,14);error.setTextColor(Color.rgb(151,87,42));panel.addView(error);
        Button connect=button("连接服务");panel.addView(connect);connect.setOnClickListener(v->{try{String target=ServerAddress.normalize(address.getText().toString());connect(target);}catch(IllegalArgumentException invalid){error.setText(invalid.getMessage());}});
        panel.addView(text("请只连接你或团队管理的服务。模型密钥保留在服务器，手机无需填写。",13));
    }
    private void switchServer(){new AlertDialog.Builder(this).setTitle("切换服务").setMessage("将清除本机登录状态和页面数据，服务器上的任务与记录保留。未提交的问题需要重新填写。").setNegativeButton("取消",null).setPositiveButton("切换",(d,w)->{
        generation++;disposeWeb();WebStorage.getInstance().deleteAllData();CookieManager.getInstance().removeAllCookies(done->{CookieManager.getInstance().flush();origin="";getPreferences(MODE_PRIVATE).edit().remove("server").apply();setup("");});
    }).show();}
    private HttpsURLConnection request(String address,String cookie) throws IOException {
        HttpsURLConnection connection=(HttpsURLConnection)new URL(address).openConnection();connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(12000);connection.setReadTimeout(30000);
        connection.setRequestProperty("Accept","application/json");if(cookie!=null&&!cookie.isEmpty())connection.setRequestProperty("Cookie",cookie);return connection;
    }
    private void connect(String address){
        final String target;try{target=ServerAddress.normalize(address);}catch(IllegalArgumentException error){setup(error.getMessage());return;}
        final int current=++generation;disposeWeb();body.removeAllViews();body.addView(text("正在连接服务…",16));progress.setVisibility(View.VISIBLE);
        io.execute(()->{String problem=null;HttpsURLConnection connection=null;
            try{connection=request(target+"api/auth/session",null);if(connection.getResponseCode()!=200)throw new IOException();
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[2048];try(InputStream input=connection.getInputStream()){int size;while((size=input.read(buffer))!=-1){if(bytes.size()+size>16384)throw new IOException();bytes.write(buffer,0,size);}}
                if(!new JSONObject(bytes.toString(StandardCharsets.UTF_8.name())).optBoolean("auth_enabled",false))problem="服务尚未启用登录。请先在服务器启用账号认证，再连接手机。";
            }catch(Exception error){problem="连接失败，请核对服务地址、HTTPS 证书、网络和服务器状态。";}finally{if(connection!=null)connection.disconnect();}
            final String failure=problem;runOnUiThread(()->{if(isFinishing()||isDestroyed()||current!=generation)return;if(failure!=null){origin=target;setup(failure);}else{origin=target;getPreferences(MODE_PRIVATE).edit().putString("server",target).apply();openWeb();}});
        });
    }
    private void openWeb(){
        body.removeAllViews();web=new WebView(this);WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setCacheMode(WebSettings.LOAD_NO_CACHE);settings.setSupportMultipleWindows(true);settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setUserAgentString(settings.getUserAgentString()+" DevFlowAndroid/0.18.1");CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return route(request.getUrl().toString(),request.hasGesture());}
            @Override public boolean shouldOverrideUrlLoading(WebView view,String url){return route(url,true);}
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap icon){if(view==web)progress.setVisibility(View.VISIBLE);}
            @Override public void onPageFinished(WebView view,String url){if(view==web){progress.setVisibility(View.GONE);CookieManager.getInstance().flush();}}
            @Override public void onReceivedSslError(WebView view,SslErrorHandler handler,SslError error){handler.cancel();if(view==web)setup("HTTPS 证书校验失败，请修复服务器证书后重试。");}
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(view==web&&request.isForMainFrame())setup("暂时连接不上服务。联网后重新连接，已提交的任务仍在服务器保留。");}
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public void onProgressChanged(WebView view,int value){progress.setProgress(value);}
            @Override public boolean onCreateWindow(WebView view,boolean dialog,boolean userGesture,Message result){
                if(!userGesture)return false;WebView popup=new WebView(MainActivity.this);popup.setWebViewClient(new WebViewClient(){
                    @Override public boolean shouldOverrideUrlLoading(WebView ignored,WebResourceRequest request){String target=request.getUrl().toString();if(ServerAddress.sameOrigin(origin,target)){if(web!=null)web.loadUrl(target);}else route(target,true);popup.post(popup::destroy);return true;}
                    @Override public boolean shouldOverrideUrlLoading(WebView ignored,String target){if(ServerAddress.sameOrigin(origin,target)){if(web!=null)web.loadUrl(target);}else route(target,true);popup.post(popup::destroy);return true;}
                });((WebView.WebViewTransport)result.obj).setWebView(popup);result.sendToTarget();return true;
            }
        });
        web.setDownloadListener((url,agent,disposition,mime,length)->{
            if(!ServerAddress.sameOrigin(origin,url)){Toast.makeText(this,"只能导出当前服务的数据。",Toast.LENGTH_LONG).show();return;}
            pendingDownload=url;Intent create=new Intent(Intent.ACTION_CREATE_DOCUMENT);create.addCategory(Intent.CATEGORY_OPENABLE);create.setType("application/json");create.putExtra(Intent.EXTRA_TITLE,"devflow-export.json");startActivityForResult(create,SAVE_EXPORT);
        });
        body.addView(web,new LinearLayout.LayoutParams(-1,-1));web.loadUrl(origin);
    }
    private boolean route(String target,boolean gesture){
        if(ServerAddress.sameOrigin(origin,target))return false;
        if(gesture&&ServerAddress.externalHttps(target)){try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(target)));}catch(Exception error){Toast.makeText(this,"未找到可打开链接的浏览器。",Toast.LENGTH_SHORT).show();}}
        return true;
    }
    @Override protected void onActivityResult(int code,int result,Intent data){
        super.onActivityResult(code,result,data);if(code!=SAVE_EXPORT)return;String target=pendingDownload;pendingDownload=null;
        if(result!=RESULT_OK||data==null||data.getData()==null||target==null||!ServerAddress.sameOrigin(origin,target))return;
        Uri destination=data.getData();String cookie=CookieManager.getInstance().getCookie(target);
        io.execute(()->{boolean success=false;HttpsURLConnection connection=null;
            try{connection=request(target,cookie);if(connection.getResponseCode()!=200)throw new IOException();String type=connection.getContentType();if(type==null||!type.toLowerCase(java.util.Locale.ROOT).contains("application/json"))throw new IOException();
                try(InputStream input=connection.getInputStream();OutputStream output=getContentResolver().openOutputStream(destination,"wt")){if(output==null)throw new IOException();byte[] buffer=new byte[8192];int total=0,size;while((size=input.read(buffer))!=-1){total+=size;if(total>MAX_EXPORT)throw new IOException();output.write(buffer,0,size);}}success=true;
            }catch(Exception error){try{DocumentsContract.deleteDocument(getContentResolver(),destination);}catch(Exception ignored){}}finally{if(connection!=null)connection.disconnect();}
            final boolean saved=success;runOnUiThread(()->Toast.makeText(this,saved?"导出已保存。":"导出未完成，请联网并确认登录后重试。",Toast.LENGTH_LONG).show());
        });
    }
    private void back(){if(web!=null&&web.canGoBack())web.goBack();else finish();}
    @Override public void onBackPressed(){back();}
    @Override protected void onDestroy(){generation++;disposeWeb();io.shutdownNow();super.onDestroy();}
}
