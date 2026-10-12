package app.alberich.widget;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import org.json.JSONObject;

/** Fires when one of the planned reminders is due. */
public class NotifyReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent in) {
        String kind = in.getStringExtra("kind"), act = in.getStringExtra("act");
        if ("credit_unlocked".equals(kind) && act != null && !act.isEmpty()) {
            JSONObject t = Local.tile(c, act);                               // the same rule as the computer: say nothing if there is nothing to spend or you are already over
            if (t == null || !"available".equals(t.optString("status"))) return;
        }
        String id = in.getStringExtra("id");
        Notify.post(c, in.getStringExtra("title"), in.getStringExtra("body"), in.getStringExtra("mood"), "credit_unlocked".equals(kind) ? act : "", id == null ? 1 : (id.hashCode() & 0x7FFFFFFF) % 100000 + 100);
    }
}
