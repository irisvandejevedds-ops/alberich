package app.alberich.widget;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class Store {
    private Store() {}

    static SharedPreferences p(Context c) { return c.getSharedPreferences("alberich", Context.MODE_PRIVATE); }
    static String url(Context c) { return p(c).getString("url", ""); }
    static String token(Context c) { return p(c).getString("token", ""); }
    static boolean connected(Context c) { return !url(c).isEmpty() && !token(c).isEmpty(); }

    static void saveConn(Context c, String url, String token) {
        url = url.trim();
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        p(c).edit().putString("url", url).putString("token", token.trim()).apply();
    }

    static String cache(Context c) { return p(c).getString("cache", ""); }
    static void saveCache(Context c, String json) { p(c).edit().putString("cache", json).putLong("cache_ts", System.currentTimeMillis()).apply(); }
    /** For a tap that only adjusts the saved copy while offline: it is still as old as it was. */
    static void saveCacheKeepAge(Context c, String json) { p(c).edit().putString("cache", json).apply(); }
    static long cacheAgeMs(Context c) { long t = p(c).getLong("cache_ts", 0); return t == 0 ? Long.MAX_VALUE : System.currentTimeMillis() - t; }

    static synchronized JSONArray queue(Context c) {
        try { return new JSONArray(p(c).getString("queue", "[]")); } catch (JSONException e) { return new JSONArray(); }
    }
    static synchronized void saveQueue(Context c, JSONArray q) { p(c).edit().putString("queue", q.toString()).apply(); }
    static synchronized void enqueue(Context c, JSONObject item) { JSONArray q = queue(c); q.put(item); saveQueue(c, q); }
    static synchronized int pending(Context c) { return queue(c).length(); }
}
