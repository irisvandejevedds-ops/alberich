package app.alberich.widget;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/** The app: the Alberich pages, bundled inside this APK and shown full screen. They are served from the app's own files under a made-up address that never
 *  reaches the network, so nothing is downloaded, and the page can talk to the phone's own storage only. Links to anywhere else open in the browser. */
public class WebActivity extends Activity {
    static final String HOST = "alberich.invalid";
    static final String ORIGIN = "https://" + HOST + "/";

    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(Color.parseColor("#4A2E18"));
        getWindow().setNavigationBarColor(Color.parseColor("#4A2E18"));
        Local.ensurePeriodic(this);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#4A2E18"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(false);
        s.setGeolocationEnabled(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        web.addJavascriptInterface(new Bridge(this), "AlberichNative");
        web.setWebViewClient(new Client(this));
        setContentView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        if (b != null && web.restoreState(b) != null) return;
        web.loadUrl(target(getIntent()));
    }

    private String target(Intent i) {
        String hash = i == null ? null : i.getStringExtra("hash");
        return ORIGIN + "index.html" + (hash != null && hash.matches("#/[a-z]{0,20}") ? hash : "");
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        if (web != null) web.loadUrl(target(i));
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        if (web != null) web.saveState(out);
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            ((ViewGroup) web.getParent()).removeView(web);
            web.destroy();
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    boolean ours(Uri u) { return u != null && "https".equals(u.getScheme()) && HOST.equalsIgnoreCase(u.getHost()); }

    void openOutside(Uri u) {
        String sc = u.getScheme();
        if (!("https".equals(sc) || "http".equals(sc) || "mailto".equals(sc))) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (ActivityNotFoundException e) { /* nothing can open it */ }
    }

    /** Serve one of the bundled files, or a 404. Only plain file names under assets/web are reachable. */
    WebResourceResponse asset(Uri u) {
        String path = u.getPath() == null ? "" : u.getPath();
        if (path.isEmpty() || path.equals("/")) path = "/index.html";
        String name = path.substring(1);
        if (!name.matches("[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?") || name.contains("..")) return notFound();
        try {
            InputStream in = getAssets().open("web/" + name);
            String ext = MimeTypeMap.getFileExtensionFromUrl(name);
            String mime = ext.equals("js") ? "text/javascript" : ext.equals("woff2") ? "font/woff2" : ext.equals("webmanifest") ? "application/manifest+json" : MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
            WebResourceResponse r = new WebResourceResponse(mime == null ? "application/octet-stream" : mime, "utf-8", in);
            Map<String, String> h = new HashMap<>();
            h.put("Cache-Control", "no-store");
            r.setResponseHeaders(h);
            return r;
        } catch (IOException e) { return notFound(); }
    }

    private WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not found", new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
    }

    static final class Client extends WebViewClient {
        private final WebActivity a;
        Client(WebActivity a) { this.a = a; }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
            return a.ours(r.getUrl()) ? a.asset(r.getUrl()) : new WebResourceResponse("text/plain", "utf-8", 403, "Blocked", new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            if (a.ours(r.getUrl())) return false;
            a.openOutside(r.getUrl());
            return true;
        }
    }
}
