package app.alberich.widget;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.BitmapFactory;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** Alberich's reminders, delivered by the phone itself. The app works out the next five weeks of them and hands the plan over (saveSchedule); Android's alarm clock
 *  wakes this up at each time, so they arrive with no internet and no server. Using the app or the widget re-plans the neglect nags from that moment. */
final class Notify {
    private Notify() {}

    static final String CHANNEL = "ravens";
    private static final long DAY = 86400000L;
    private static final int MAX_ALARMS = 300;

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences("alberich-notify", Context.MODE_PRIVATE); }

    static void ensureChannel(Context c) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm.getNotificationChannel(CHANNEL) == null) nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Alberich's ravens", NotificationManager.IMPORTANCE_DEFAULT));
    }

    static boolean allowed(Context c) { return ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).areNotificationsEnabled(); }

    /** Remember the plan and arm it. */
    static void saveSchedule(Context c, String json) {
        prefs(c).edit().putString("schedule", json).commit();
        arm(c);
    }

    /** The user just used the app or the widget: the idle clock restarts, so the nags move out. */
    static void touch(Context c) {
        Db.get(c).setSetting("last_interaction", Db.nowIso());
        arm(c);
    }

    static void arm(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        SharedPreferences p = prefs(c);
        int old = p.getInt("armed", 0);
        for (int i = 0; i < old; i++) {
            PendingIntent pi = PendingIntent.getBroadcast(c, 5000 + i, new Intent(c, NotifyReceiver.class), PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
            if (pi != null) { am.cancel(pi); pi.cancel(); }
        }
        int n = 0;
        try {
            JSONArray list = new JSONArray(p.getString("schedule", "[]"));
            long now = System.currentTimeMillis();
            String li = Db.get(c).setting("last_interaction");
            long last = li == null ? now : Db.parseIso(li);
            for (int i = 0; i < list.length() && n < MAX_ALARMS; i++) {
                JSONObject it = list.getJSONObject(i);
                long at = "neglect_nag".equals(it.optString("kind")) && it.has("idleDays") ? last + (long) (it.optDouble("idleDays") * DAY) : it.optLong("at");
                if (at <= now + 1000) continue;
                Intent in = new Intent(c, NotifyReceiver.class).putExtra("title", it.optString("title")).putExtra("body", it.optString("body")).putExtra("mood", it.optString("mood", "neutral"))
                        .putExtra("kind", it.optString("kind")).putExtra("act", it.isNull("activityId") ? "" : it.optString("activityId")).putExtra("id", it.optString("id"));
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, PendingIntent.getBroadcast(c, 5000 + n, in, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
                n++;
            }
        } catch (JSONException e) { /* nothing to arm */ }
        p.edit().putInt("armed", n).commit();
    }

    private static int moodRes(Context c, String mood) {
        int r = c.getResources().getIdentifier("mood_" + mood, "drawable", c.getPackageName());
        return r != 0 ? r : c.getResources().getIdentifier("mood_neutral", "drawable", c.getPackageName());
    }

    /** Show a notification now. act (may be empty) adds a "Log it" button. */
    static void post(Context c, String title, String body, String mood, String act, int notifId) {
        if (!allowed(c)) return;
        ensureChannel(c);
        Notification.Builder b = new Notification.Builder(c, CHANNEL).setSmallIcon(c.getResources().getIdentifier("badge", "drawable", c.getPackageName()))
                .setLargeIcon(BitmapFactory.decodeResource(c.getResources(), moodRes(c, mood))).setContentTitle(title).setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body)).setAutoCancel(true)
                .setContentIntent(PendingIntent.getActivity(c, 1, new Intent(c, WebActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        if (act != null && !act.isEmpty()) {
            Intent log = new Intent(c, LogReceiver.class).putExtra("id", act).putExtra("name", Db.get(c).actName(act)).putExtra("notif", notifId);
            b.addAction(new Notification.Action.Builder(null, "Log it", PendingIntent.getBroadcast(c, 9000 + (act.hashCode() & 0xFFF), log, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE)).build());
        }
        ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).notify(notifId, b.build());
    }
}
