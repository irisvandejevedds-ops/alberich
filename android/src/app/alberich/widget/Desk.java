package app.alberich.widget;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.text.DateFormat;
import java.util.Date;
import java.util.List;
import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** The optional link to Alberich on a computer: pairing, and keeping the two copies of the pouches and taps in step. The phone works fine without it. */
final class Desk {
    private Desk() {}

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences("alberich-desk", Context.MODE_PRIVATE); }

    static volatile boolean running = false;

    static boolean paired(Context c) { return !prefs(c).getString("key", "").isEmpty(); }

    static void unpair(Context c) { prefs(c).edit().clear().commit(); }

    /** "name (host, host)" for a valid pairing link, or null. Used to ask before pairing from a link someone else could have sent. */
    static String describe(String link) {
        try {
            Uri u = Uri.parse(link == null ? "" : link.trim());
            if (!"alberich".equals(u.getScheme()) || !"pair".equals(u.getHost()) || u.getQueryParameter("key") == null) return null;
            StringBuilder hosts = new StringBuilder();
            for (String e : u.getQueryParameters("e")) {
                String url = e.indexOf('#') >= 0 ? e.substring(0, e.indexOf('#')) : e;
                if (!url.startsWith("https://")) continue;
                String h = Uri.parse(url).getHost();
                if (h != null) hosts.append(hosts.length() > 0 ? ", " : "").append(h);
            }
            String name = u.getQueryParameter("name");
            return (name == null || name.isEmpty() ? "a computer" : name) + (hosts.length() > 0 ? " (" + hosts + ")" : "");
        } catch (RuntimeException e) { return null; }
    }

    /** Read a pairing link (alberich://pair?...). Returns "" when it worked, otherwise a message to show. */
    static String pair(Context c, String link) {
        Uri u;
        try { u = Uri.parse(link == null ? "" : link.trim()); } catch (RuntimeException e) { return "That is not a pairing link."; }
        if (!"alberich".equals(u.getScheme()) || !"pair".equals(u.getHost())) return "That is not an Alberich pairing link.";
        String key = u.getQueryParameter("key");
        if (key == null || !key.startsWith("als_") || key.length() > 100) return "The pairing link has no valid key.";
        JSONArray eps = new JSONArray();
        List<String> raw = u.getQueryParameters("e");
        try {
            for (String e : raw) {
                String url = e, pin = "";
                int h = e.indexOf('#');
                if (h >= 0) { url = e.substring(0, h); pin = e.substring(h + 1).toLowerCase(); }
                if (!url.startsWith("https://") || url.length() > 200) continue;
                if (!pin.isEmpty() && !pin.matches("[0-9a-f]{64}")) continue;
                eps.put(new JSONObject().put("url", url).put("pin", pin));
            }
            if (eps.length() == 0) return "The pairing link has no address for the computer.";
            String name = u.getQueryParameter("name");
            prefs(c).edit().clear().putString("key", key).putString("name", name == null ? "your computer" : name).putString("eps", eps.toString()).putInt("cursor_acts", 0).putInt("cursor_logs", 0).commit();
        } catch (JSONException e) { return "That pairing link is damaged."; }
        return "";
    }

    /** The first address that has a normal certificate (a Tailscale one): the one a browser can open. */
    static String webUrl(Context c) {
        try {
            JSONArray eps = new JSONArray(prefs(c).getString("eps", "[]"));
            for (int i = 0; i < eps.length(); i++) if (eps.getJSONObject(i).optString("pin", "").isEmpty()) return eps.getJSONObject(i).getString("url");
        } catch (JSONException e) { /* none */ }
        return null;
    }

    static boolean openComputer(Context c) {
        String url = webUrl(c);
        if (url == null) return false;
        try { c.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return true; } catch (RuntimeException e) { return false; }
    }

    static JSONObject status(Context c) {
        SharedPreferences p = prefs(c);
        JSONObject o = new JSONObject();
        try {
            long t = p.getLong("last_ok", 0);
            o.put("paired", paired(c)).put("name", p.getString("name", "")).put("message", p.getString("message", "")).put("running", running).put("web", webUrl(c) != null)
                    .put("last_sync", t == 0 ? "" : DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(new Date(t)));
        } catch (JSONException e) { /* cannot happen */ }
        return o;
    }

    /** True when the last good sync is more than two days old (or there has not been one): the widget's refresh arrow turns amber. */
    static boolean stale(Context c) {
        if (!paired(c)) return false;
        long t = prefs(c).getLong("last_ok", 0);
        return t == 0 || System.currentTimeMillis() - t > 2 * 24 * 3600 * 1000L;
    }

    private static void note(Context c, String msg) { prefs(c).edit().putString("message", msg).commit(); }

    /** Send what changed here, take what changed there. Returns "" on success, otherwise why not. Runs on the caller's thread. */
    static String sync(Context c) {
        if (!paired(c)) return "Not paired.";
        synchronized (Desk.class) {
            running = true;
            try {
                String why = "";
                try {
                    JSONArray eps = new JSONArray(prefs(c).getString("eps", "[]"));
                    for (int i = 0; i < eps.length(); i++) {
                        JSONObject ep = eps.getJSONObject(i);
                        try { exchange(c, ep.getString("url"), ep.optString("pin", "")); note(c, ""); prefs(c).edit().putLong("last_ok", System.currentTimeMillis()).commit(); return ""; }
                        catch (IOException e) { why = e instanceof java.net.SocketTimeoutException ? "the computer did not answer" : "could not reach the computer"; }
                        catch (SecurityException e) { why = e.getMessage(); break; }
                    }
                } catch (JSONException e) { why = "the pairing is damaged; pair again"; }
                note(c, why + ". Is the computer on, and on this Wi-Fi or Tailscale?");
                return why;
            } finally {
                running = false;
                Notify.arm(c);
                PouchWidget.refreshAll(c);
            }
        }
    }

    private static void exchange(Context c, String base, String pin) throws IOException {
        SharedPreferences p = prefs(c);
        Db db = Db.get(c);
        String key = p.getString("key", "");
        int curA = p.getInt("cursor_acts", 0), curL = p.getInt("cursor_logs", 0);
        boolean pushedOnce = false;
        for (int round = 0; round < 12; round++) {
            JSONArray outA = pushedOnce ? new JSONArray() : db.acts(true), outL = pushedOnce ? new JSONArray() : db.logs(true);
            try {
                JSONObject mine = new JSONObject();
                String pet = db.setting("pet"), li = db.setting("last_interaction");
                if (pet != null) mine.put("pet", pet);
                if (li != null) mine.put("last_interaction", li);
                JSONObject body = new JSONObject().put("since_acts", curA).put("since_logs", curL).put("acts", outA).put("logs", outL).put("settings", mine);
                JSONObject reply = new JSONObject(post(base + "/api/sync", key, body.toString(), pin));
                if (!reply.optBoolean("ok")) throw new IOException("the computer refused");
                db.markSent("acts", outA); db.markSent("logs", outL);
                pushedOnce = true;
                JSONArray ia = reply.optJSONArray("acts"), il = reply.optJSONArray("logs");
                for (int i = 0; ia != null && i < ia.length(); i++) { JSONObject a = ia.getJSONObject(i); if (!a.optString("id").isEmpty() && !a.optString("name").isEmpty()) db.putActFromDesk(a); }
                for (int i = 0; il != null && i < il.length(); i++) { JSONObject l = il.getJSONObject(i); if (!l.optString("id").isEmpty() && !l.optString("activityId").isEmpty() && !l.optString("ts").isEmpty()) db.putLogFromDesk(l); }
                curA = reply.optInt("cursor_acts", curA); curL = reply.optInt("cursor_logs", curL);
                p.edit().putInt("cursor_acts", curA).putInt("cursor_logs", curL).commit();
                mergeSettings(db, reply.optJSONObject("settings"));
                if (!reply.optBoolean("more")) return;
            } catch (JSONException e) { throw new IOException("unreadable answer"); }
        }
    }

    /** What only the computer can work out replaces the phone's copy; the pet's wellbeing and the last-opened time keep whichever is newer. */
    private static void mergeSettings(Db db, JSONObject s) throws JSONException {
        if (s == null) return;
        for (String k : new String[]{"buffer_cents", "stmt_uses"}) if (s.has(k)) db.setSetting(k, s.getString(k));
        if (s.has("periods")) {
            JSONObject mine;
            try { mine = new JSONObject(db.setting("periods") == null ? "{}" : db.setting("periods")); } catch (JSONException e) { mine = new JSONObject(); }
            JSONObject theirs = new JSONObject(s.getString("periods"));
            java.util.Iterator<String> it = theirs.keys();
            while (it.hasNext()) { String k = it.next(); mine.put(k, theirs.get(k)); }
            db.setSetting("periods", mine.toString());
        }
        String pet = s.optString("pet", "");
        if (!pet.isEmpty()) {
            try {
                String mineRaw = db.setting("pet");
                if (mineRaw == null || new JSONObject(pet).getString("ts").compareTo(new JSONObject(mineRaw).optString("ts", "")) > 0) db.setSetting("pet", pet);
            } catch (JSONException e) { /* keep ours */ }
        }
        String li = s.optString("last_interaction", "");
        if (!li.isEmpty() && (db.setting("last_interaction") == null || li.compareTo(db.setting("last_interaction")) > 0)) db.setSetting("last_interaction", li);
    }

    /** POST JSON over https. A pinned address is trusted only if its certificate is exactly the pinned one; any other address uses the normal certificate check. */
    private static String post(String url, String key, String json, String pin) throws IOException {
        HttpURLConnection raw = (HttpURLConnection) new URL(url).openConnection();
        if (raw instanceof HttpsURLConnection && !pin.isEmpty()) {
            try {
                SSLContext ctx = SSLContext.getInstance("TLS");
                ctx.init(null, new TrustManager[]{new Pinned(pin)}, new SecureRandom());
                ((HttpsURLConnection) raw).setSSLSocketFactory(ctx.getSocketFactory());
                ((HttpsURLConnection) raw).setHostnameVerifier(new Anyone());
            } catch (java.security.GeneralSecurityException e) { throw new IOException("TLS is unavailable"); }
        }
        raw.setConnectTimeout(6000);
        raw.setReadTimeout(20000);
        raw.setRequestMethod("POST");
        raw.setDoOutput(true);
        raw.setRequestProperty("Content-Type", "application/json");
        raw.setRequestProperty("Authorization", "Bearer " + key);
        byte[] b = json.getBytes("UTF-8");
        raw.setFixedLengthStreamingMode(b.length);
        try (OutputStream o = raw.getOutputStream()) { o.write(b); }
        int code = raw.getResponseCode();
        if (code == 401) throw new SecurityException("this computer no longer accepts this phone; pair again");
        if (code >= 300) throw new IOException("the computer answered " + code);
        try (InputStream in = raw.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) { out.write(buf, 0, n); if (out.size() > 20_000_000) throw new IOException("answer too large"); }
            return out.toString("UTF-8");
        } finally { raw.disconnect(); }
    }

    static final class Pinned implements X509TrustManager {
        private final String pin;
        Pinned(String pin) { this.pin = pin; }
        @Override public void checkClientTrusted(X509Certificate[] c, String t) throws CertificateException { throw new CertificateException("no client certificates"); }
        @Override public void checkServerTrusted(X509Certificate[] chain, String t) throws CertificateException {
            try {
                byte[] h = MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
                StringBuilder sb = new StringBuilder();
                for (byte x : h) sb.append(String.format("%02x", x));
                if (!sb.toString().equals(pin)) throw new CertificateException("this is not the computer you paired with");
            } catch (java.security.NoSuchAlgorithmException e) { throw new CertificateException(e); }
        }
        @Override public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    }

    static final class Anyone implements HostnameVerifier {
        @Override public boolean verify(String host, javax.net.ssl.SSLSession s) { return true; }
    }
}
