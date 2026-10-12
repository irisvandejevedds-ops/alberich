package app.alberich.widget;

import android.Manifest;
import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.JavascriptInterface;
import java.io.OutputStream;
import org.json.JSONException;
import org.json.JSONObject;

/** What the app's web pages can ask the phone to do. Only pages served from the app's own files ever see this object. */
final class Bridge {
    private final Activity act;
    private final Context c;

    Bridge(Activity a) { this.act = a; this.c = a.getApplicationContext(); }

    @JavascriptInterface
    public String loadAll() {
        Db db = Db.get(c);
        try { return new JSONObject().put("acts", db.acts(false)).put("logs", db.logs(false)).put("settings", db.settings()).toString(); }
        catch (JSONException e) { return "{}"; }
    }

    @JavascriptInterface
    public void putActivity(String json) {
        try {
            JSONObject a = new JSONObject(json);
            if (a.optString("id").isEmpty() || a.optString("name").isEmpty()) return;
            Db.get(c).putAct(a);
            Local.scheduleSync(c);
        } catch (JSONException e) { /* ignore a malformed pouch */ }
    }

    @JavascriptInterface
    public void putLog(String json) {
        try {
            JSONObject l = new JSONObject(json);
            if (l.optString("id").isEmpty() || l.optString("activityId").isEmpty() || l.optString("ts").isEmpty()) return;
            Db.get(c).putLog(l);
            Local.scheduleSync(c);
            PouchWidget.refreshAll(c);
        } catch (JSONException e) { /* ignore a malformed log */ }
    }

    @JavascriptInterface
    public void setSetting(String k, String v) {
        if (k == null || v == null || k.length() > 60 || v.length() > 400_000) return;
        Db.get(c).setSetting(k, v);
        if (k.equals("last_interaction")) Notify.arm(c);
    }

    @JavascriptInterface
    public void saveSnapshot(String json) {
        if (json == null || json.length() > 200_000) return;
        Local.saveSnapshot(c, json);
        PouchWidget.refreshAll(c);
    }

    @JavascriptInterface
    public void saveSchedule(String json) { if (json != null && json.length() < 400_000) Notify.saveSchedule(c, json); }

    @JavascriptInterface
    public void notifyNow(String title, String body, String mood) {
        if (title == null || body == null) return;
        Notify.post(c, title, body, mood == null ? "neutral" : mood, "", (int) (System.currentTimeMillis() & 0x7FFFFFF) % 100000 + 200);
    }

    @JavascriptInterface
    public boolean notificationsAllowed() { return Notify.allowed(c); }

    /** Asks Android for permission to show notifications (needed from Android 13). Returns "granted" at once if it already is, otherwise "asked". */
    @JavascriptInterface
    public String requestNotifications() {
        if (Notify.allowed(c)) return "granted";
        if (Build.VERSION.SDK_INT >= 33 && act.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            act.runOnUiThread(() -> act.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 71));
            return "asked";
        }
        return "blocked";
    }

    @JavascriptInterface
    public void deleteAll() {
        Db.get(c).deleteAll();
        Local.clearSnapshot(c);
        Notify.saveSchedule(c, "[]");
        PouchWidget.refreshAll(c);
    }

    /** Save a text file into the Downloads folder. */
    @JavascriptInterface
    public boolean saveFile(String name, String mime, String text) {
        if (name == null || !name.matches("[A-Za-z0-9._-]{1,60}") || text == null || text.length() > 20_000_000) return false;
        try {
            ContentResolver r = c.getContentResolver();
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, name);
            v.put(MediaStore.Downloads.MIME_TYPE, mime == null ? "text/plain" : mime);
            v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
            Uri u = r.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (u == null) return false;
            try (OutputStream o = r.openOutputStream(u)) { o.write(text.getBytes("UTF-8")); }
            return true;
        } catch (Exception e) { return false; }
    }

    @JavascriptInterface
    public String syncStatus() { return Desk.status(c).toString(); }

    /** Start a sync in the background and return at once; the page watches syncStatus().running. */
    @JavascriptInterface
    public void syncNow() {
        if (Desk.running) return;
        new Thread(() -> Desk.sync(c)).start();
    }

    @JavascriptInterface
    public void unpair() { Desk.unpair(c); }

    /** Returns "" when paired, otherwise the reason it did not work. A first sync starts at once. */
    @JavascriptInterface
    public String pair(String link) {
        String why = Desk.pair(c, link);
        if (why.isEmpty()) new Thread(() -> Desk.sync(c)).start();
        return why;
    }

    /** Open the full Alberich on the paired computer in the phone's browser (needs a Tailscale address). */
    @JavascriptInterface
    public boolean openComputer() { return Desk.openComputer(c); }
}
