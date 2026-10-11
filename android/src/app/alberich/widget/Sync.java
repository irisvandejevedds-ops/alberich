package app.alberich.widget;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class Sync {
    private Sync() {}

    static String nowIso() {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(new Date());
    }

    /** Why the last fetch fell back to the saved copy, or null when it reached the server. */
    static volatile String lastError = null;

    /** Fetch fresh state (one retry); falls back to the cached copy. */
    static JSONObject state(Context c) { return state(c, false); }

    /** quick = the owner is waiting on it, so give up sooner. */
    static JSONObject state(Context c, boolean quick) {
        lastError = null;
        if (Store.connected(c)) {
            for (int attempt = 0; attempt < 2; attempt++) {
                try {
                    JSONObject s = quick ? Api.call(c, "GET", "/api/state", null, 8000, 10000) : Api.call(c, "GET", "/api/state", null);
                    Store.saveCache(c, s.toString());          // stamps the time, so the widget can tell when it has gone stale
                    lastError = null;
                    return s;
                } catch (Api.HttpError e) {
                    lastError = e.code == 401 ? "the key was refused (make a new one in the app)" : "the server answered " + e.code;
                    break;
                } catch (IOException | JSONException e) {
                    lastError = e instanceof java.net.SocketTimeoutException ? "it took too long to answer" : "it could not be reached";
                }
            }
        } else lastError = "this widget is not connected yet";
        try { String s = Store.cache(c); return s.isEmpty() ? null : new JSONObject(s); } catch (JSONException e) { return null; }
    }

    /** Log one credit. Returns the server's answer, or null when it was kept for later. */
    static JSONObject log(Context c, int id) {
        String key = UUID.randomUUID().toString();
        String ts = nowIso();
        try {
            JSONObject body = new JSONObject().put("activity_id", id).put("key", key).put("ts", ts);
            JSONObject r = Api.call(c, "POST", "/api/log", body, 8000, 8000);      // inside a tap's broadcast, so shorter; failures are queued and retried
            return r;
        } catch (Api.HttpError e) {
            if (e.code >= 400 && e.code < 500 && e.code != 429) return new JSONObject();   // refused: do not keep retrying
        } catch (IOException | JSONException e) { /* offline */ }
        try {
            Store.enqueue(c, new JSONObject().put("activity_id", id).put("key", key).put("ts", ts));
        } catch (JSONException e) { /* cannot happen */ }
        applyLocal(c, id);
        scheduleRetry(c);
        return null;
    }

    /** Make the cached tile show the tap straight away while offline. */
    static void applyLocal(Context c, int id) {
        try {
            String raw = Store.cache(c);
            if (raw.isEmpty()) return;
            JSONObject s = new JSONObject(raw);
            JSONArray tiles = s.getJSONArray("tiles");
            for (int i = 0; i < tiles.length(); i++) {
                JSONObject t = tiles.getJSONObject(i);
                if (t.getInt("id") == id) {
                    t.put("remaining", t.getInt("remaining") - 1);
                    t.put("used", t.getInt("used") + 1);
                    t.put("available", Math.max(0, t.getInt("available") - 1));
                }
            }
            Store.saveCacheKeepAge(c, s.toString());
        } catch (JSONException e) { /* ignore */ }
    }

    /** Send everything kept offline, oldest first. Returns how many are still waiting. */
    static synchronized int flush(Context c) {
        JSONArray q = Store.queue(c);
        JSONArray left = new JSONArray();
        boolean stop = false;
        for (int i = 0; i < q.length(); i++) {
            JSONObject it = q.optJSONObject(i);
            if (it == null) continue;
            if (stop) { left.put(it); continue; }
            try {
                Api.call(c, "POST", "/api/log", it);
            } catch (Api.HttpError e) {
                if (!(e.code >= 400 && e.code < 500 && e.code != 429)) { left.put(it); stop = true; }
            } catch (IOException | JSONException e) { left.put(it); stop = true; }
        }
        Store.saveQueue(c, left);
        return left.length();
    }

    /** Ask for a refresh from the server as a background job. A tap on the refresh arrow is expedited so it runs at once. */
    static void scheduleSync(Context c, boolean manual) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        android.os.PersistableBundle x = new android.os.PersistableBundle();
        x.putInt("manual", manual ? 1 : 0);
        JobInfo.Builder b = new JobInfo.Builder(manual ? 8 : 10, new ComponentName(c, SyncService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setExtras(x);
        if (manual && android.os.Build.VERSION.SDK_INT >= 31) b.setExpedited(true);
        js.schedule(b.build());
    }

    /** Keep refreshing about every 30 minutes, whether or not Android calls the widget's own update. */
    static void ensurePeriodic(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null || js.getPendingJob(9) != null) return;
        js.schedule(new JobInfo.Builder(9, new ComponentName(c, SyncService.class)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(30 * 60 * 1000L).build());
    }

    static void scheduleRetry(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        js.schedule(new JobInfo.Builder(7, new ComponentName(c, RetryService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setBackoffCriteria(60000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).setPersisted(false).build());
    }
}
