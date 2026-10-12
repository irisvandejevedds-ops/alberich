package app.alberich.widget;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.widget.Toast;
import org.json.JSONObject;

/** A tap on a pouch in the widget (or "Log it" on a reminder): saved on the phone at once, with no network. */
public class LogReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        String id = intent.getStringExtra("id"), name = intent.getStringExtra("name") == null ? "That" : intent.getStringExtra("name");
        if (id == null || id.isEmpty()) return;
        JSONObject t = Local.logTap(c, id);
        int notif = intent.getIntExtra("notif", -1);
        if (notif >= 0) ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(notif);
        String msg;
        if (t == null) msg = name + " is no longer in the Purse.";
        else {
            String st = t.optString("status");
            int left = t.optInt("remaining");
            msg = "overdrawn".equals(st) ? name + " noted, but you are over the plan." : name + " noted. " + Math.max(0, left) + " left.";
        }
        Toast.makeText(c, msg, Toast.LENGTH_SHORT).show();
    }
}
