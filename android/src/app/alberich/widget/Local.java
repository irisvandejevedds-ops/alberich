package app.alberich.widget;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** What the widget shows, worked out from the phone's own data, and the one way the widget logs a tap. */
final class Local {
    private Local() {}

    static final int JOB_SYNC = 10, JOB_PERIODIC = 9;

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences("alberich-local", Context.MODE_PRIVATE); }

    /** The app saves its latest summary of every pouch here after each change; the widget keeps it current from the clock and from its own taps. */
    static void saveSnapshot(Context c, String json) { prefs(c).edit().putString("snapshot", json).commit(); }
    static void clearSnapshot(Context c) { prefs(c).edit().remove("snapshot").commit(); }

    /** {mood, tiles: [{id, name, icon, status, remaining, allotted, available}]} as of now. */
    static JSONObject state(Context c) {
        JSONObject out = new JSONObject();
        JSONArray tiles = new JSONArray();
        String mood = "neutral";
        try {
            String raw = prefs(c).getString("snapshot", "");
            if (!raw.isEmpty()) {
                JSONObject snap = new JSONObject(raw);
                mood = snap.optString("mood", "neutral");
                long ts = snap.optLong("ts", 0), nowMs = System.currentTimeMillis();
                boolean sameMonth = monthOf(ts).equals(monthOf(nowMs));
                String since = Db.iso(new java.util.Date(ts));
                JSONArray src = snap.optJSONArray("tiles");
                for (int i = 0; src != null && i < src.length(); i++) {
                    JSONObject t = src.getJSONObject(i);
                    int allotted = t.optInt("allotted"), used = sameMonth ? t.optInt("used") + Db.get(c).tapsSince(t.getString("id"), since) : 0, unlocked = 0;
                    JSONArray un = t.optJSONArray("unlocks");
                    for (int k = 0; sameMonth && un != null && k < un.length(); k++) if (un.optLong(k) <= nowMs) unlocked++;
                    int available = Math.max(0, Math.min(unlocked, allotted) - used), remaining = allotted - used;
                    String status = allotted <= 0 ? "unset" : used > allotted ? "overdrawn" : remaining == 0 ? "done" : available > 0 ? "available" : "waiting";
                    tiles.put(new JSONObject().put("id", t.getString("id")).put("name", t.optString("name")).put("icon", t.optString("icon", "star")).put("status", status)
                            .put("remaining", remaining).put("allotted", allotted).put("available", available));
                }
            }
            out.put("mood", mood).put("tiles", tiles);
        } catch (JSONException e) { /* an empty state is fine */ }
        return out;
    }

    private static String monthOf(long ms) {
        java.util.Calendar cal = java.util.Calendar.getInstance();
        cal.setTimeInMillis(ms);
        return cal.get(java.util.Calendar.YEAR) + "-" + cal.get(java.util.Calendar.MONTH);
    }

    static JSONObject tile(Context c, String id) {
        JSONArray t = state(c).optJSONArray("tiles");
        for (int i = 0; t != null && i < t.length(); i++) if (id.equals(t.optJSONObject(i).optString("id"))) return t.optJSONObject(i);
        return null;
    }

    /** Log one tap on a pouch. Never touches the network. Returns the pouch's tile afterwards (or null if the pouch is unknown). */
    static JSONObject logTap(Context c, String actId) {
        if (Db.get(c).actName(actId) == null) return null;
        try {
            String now = Db.nowIso();
            Db.get(c).putLog(new JSONObject().put("id", Db.uuid()).put("activityId", actId).put("ts", now).put("source", "live").put("note", JSONObject.NULL).put("undoneAt", JSONObject.NULL).put("updatedAt", now));
        } catch (JSONException e) { return null; }
        Notify.touch(c);
        scheduleSync(c);
        PouchWidget.refreshAll(c);
        return tile(c, actId);
    }

    /** Ask for a sync soon (only does anything when a computer is paired). */
    static void scheduleSync(Context c) {
        if (!Desk.paired(c)) return;
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        js.schedule(new JobInfo.Builder(JOB_SYNC, new ComponentName(c, SyncService.class)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).build());
    }

    /** Repaint about every 30 minutes (the tiles change as credits unlock), and sync if paired. Needs no network to run. */
    static void ensurePeriodic(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null || js.getPendingJob(JOB_PERIODIC) != null) return;
        js.schedule(new JobInfo.Builder(JOB_PERIODIC, new ComponentName(c, SyncService.class)).setPeriodic(30 * 60 * 1000L).build());
    }
}
