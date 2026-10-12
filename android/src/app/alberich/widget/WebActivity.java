package app.alberich.widget;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.MimeTypeMap;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.List;

/** The app itself: the same pages you get in the browser, shown full screen inside this app, so the widget and the app are one install.
 *  It only ever loads the address you connected with; links to anywhere else open in the browser. */
public class WebActivity extends Activity {
    private static final int REQ_FILES = 41;
    private static final String FOLDER = "Alberich";

    private WebView web;
    private View errorView;
    private TextView errorText;
    private ValueCallback<Uri[]> chooser;
    private Uri cameraUri;
    private Uri base;
    boolean loadFailed;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (!Store.connected(this)) {
            startActivity(new Intent(this, MainActivity.class));
            finish();
            return;
        }
        base = Uri.parse(Store.url(this));
        getWindow().setStatusBarColor(Color.parseColor("#4A2E18"));
        getWindow().setNavigationBarColor(Color.parseColor("#4A2E18"));

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#4A2E18"));
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setBackgroundColor(Color.parseColor("#4A2E18"));
        web.setWebViewClient(new Client(this));
        web.setWebChromeClient(new Chrome(this));
        web.setDownloadListener((url, ua, disposition, mime, length) -> download(url, ua, disposition, mime));
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        errorView = buildError();
        errorView.setVisibility(View.GONE);
        root.addView(errorView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        if (b != null && web.restoreState(b) != null) return;
        web.loadUrl(target(getIntent()));
    }

    private android.graphics.Typeface pixelFace() {
        return android.graphics.Typeface.createFromAsset(getAssets(), "pixel.ttf");
    }

    private String target(Intent i) {
        String path = i == null ? null : i.getStringExtra("path");
        if (path != null && path.startsWith("/") && !path.startsWith("//")) return Store.url(this) + path;
        return Store.url(this);
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
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
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
        if (errorView.getVisibility() == View.VISIBLE) { super.onBackPressed(); return; }
        if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    /** True for addresses on the connected server. */
    boolean ours(Uri u) {
        return u != null && base != null && "https".equals(u.getScheme()) && base.getHost() != null && base.getHost().equalsIgnoreCase(u.getHost()) && base.getPort() == u.getPort();
    }

    void openOutside(Uri u) {
        String sc = u.getScheme();
        if (!("https".equals(sc) || "http".equals(sc) || "mailto".equals(sc) || "tel".equals(sc))) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (ActivityNotFoundException e) { /* nothing can open it */ }
    }

    void showError(String why) {
        loadFailed = true;
        errorText.setText("Couldn't reach Alberich.\n\n" + why + "\n\nCheck that Tailscale is on, then try again.");
        errorView.setVisibility(View.VISIBLE);
    }

    void hideError() { errorView.setVisibility(View.GONE); }

    private View buildError() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER);
        col.setBackgroundColor(Color.parseColor("#4A2E18"));
        int pad = Math.round(24 * getResources().getDisplayMetrics().density);
        col.setPadding(pad, pad, pad, pad);
        errorText = new TextView(this);
        errorText.setTextColor(Color.parseColor("#F4E3C4"));
        errorText.setTextSize(17);
        errorText.setTypeface(pixelFace());
        errorText.setGravity(Gravity.CENTER);
        col.addView(errorText);
        col.addView(button("Try again", v -> { loadFailed = false; hideError(); web.reload(); }));
        col.addView(button("Connection settings", v -> startActivity(new Intent(this, MainActivity.class).putExtra("settings", true))));
        return col;
    }

    private Button button(String label, View.OnClickListener l) {
        Button bt = new Button(this);
        bt.setText(label);
        bt.setAllCaps(true);
        bt.setTypeface(pixelFace(), android.graphics.Typeface.BOLD);
        bt.setTextColor(Color.WHITE);
        bt.setBackgroundColor(Color.parseColor("#B07A45"));
        bt.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, Math.round(46 * getResources().getDisplayMetrics().density));
        lp.topMargin = Math.round(14 * getResources().getDisplayMetrics().density);
        bt.setLayoutParams(lp);
        return bt;
    }

    // ---------- downloads (the CSV export, the widget file) ----------
    private void download(String url, String ua, String disposition, String mime) {
        if (!ours(Uri.parse(url))) { openOutside(Uri.parse(url)); return; }
        try {
            String name = URLUtil.guessFileName(url, disposition, mime);
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) r.addRequestHeader("Cookie", cookie);
            if (ua != null) r.addRequestHeader("User-Agent", ua);
            r.setMimeType(mime).setTitle(name).setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, "Downloading " + name + " to your Downloads folder.", Toast.LENGTH_SHORT).show();
        } catch (RuntimeException e) {
            Toast.makeText(this, "Couldn't start that download.", Toast.LENGTH_LONG).show();
        }
    }

    // ---------- file and photo pickers ----------
    boolean chooseFiles(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams p) {
        if (chooser != null) chooser.onReceiveValue(null);
        chooser = callback;
        cameraUri = null;
        Intent intent;
        if (p.isCaptureEnabled()) {
            ContentValues cv = new ContentValues();
            cv.put(MediaStore.Images.Media.DISPLAY_NAME, "photo-" + System.currentTimeMillis() + ".jpg");
            cv.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
            cv.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/" + FOLDER);
            cameraUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
            intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            if (cameraUri != null) {
                intent.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
                intent.setClipData(ClipData.newRawUri("", cameraUri));
                intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            }
        } else {
            intent = new Intent(Intent.ACTION_GET_CONTENT);
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            List<String> types = new ArrayList<>();
            boolean unknown = false;
            for (String t : p.getAcceptTypes()) {
                t = t == null ? "" : t.trim();
                if (t.isEmpty()) continue;
                if (t.startsWith(".")) {
                    String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension(t.substring(1).toLowerCase());
                    if (m == null) unknown = true; else if (!types.contains(m)) types.add(m);
                } else if (!types.contains(t)) types.add(t);
            }
            if (types.isEmpty() || unknown) intent.setType("*/*");
            else if (types.size() == 1) intent.setType(types.get(0));
            else { intent.setType("*/*"); intent.putExtra(Intent.EXTRA_MIME_TYPES, types.toArray(new String[0])); }
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, p.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE);
        }
        try {
            startActivityForResult(intent, REQ_FILES);
            return true;
        } catch (RuntimeException e) {
            dropCamera();
            chooser = null;
            callback.onReceiveValue(null);
            Toast.makeText(this, "Couldn't open the picker.", Toast.LENGTH_LONG).show();
            return false;
        }
    }

    private void dropCamera() {
        if (cameraUri != null) {
            try { getContentResolver().delete(cameraUri, null, null); } catch (RuntimeException e) { /* already gone */ }
            cameraUri = null;
        }
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req != REQ_FILES || chooser == null) return;
        Uri[] picked = null;
        if (result == RESULT_OK) {
            boolean viaCamera = cameraUri != null && (data == null || (data.getData() == null && data.getClipData() == null));
            picked = viaCamera ? new Uri[]{cameraUri} : WebChromeClient.FileChooserParams.parseResult(result, data);
            if (!viaCamera) cameraUri = null;
        } else {
            dropCamera();
        }
        ValueCallback<Uri[]> cb = chooser;
        chooser = null;
        cameraUri = null;
        cb.onReceiveValue(picked);
    }

    // ---------- the pieces the web view calls back into (named classes: anonymous ones trip this build's dex step) ----------
    static final class Client extends WebViewClient {
        private final WebActivity a;
        Client(WebActivity a) { this.a = a; }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (a.ours(u)) return false;
            a.openOutside(u);
            return true;
        }

        @Override
        public void onPageStarted(WebView v, String url, Bitmap icon) { a.loadFailed = false; }

        @Override
        public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            if (r.isForMainFrame()) a.showError(String.valueOf(e.getDescription()));
        }

        @Override
        public void onPageFinished(WebView v, String url) { if (!a.loadFailed) a.hideError(); }
    }

    static final class Chrome extends WebChromeClient {
        private final WebActivity a;
        Chrome(WebActivity a) { this.a = a; }

        @Override
        public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) { return a.chooseFiles(cb, p); }
    }
}
