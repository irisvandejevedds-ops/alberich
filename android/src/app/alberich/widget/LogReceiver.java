package app.alberich.widget;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import org.json.JSONObject;

public class LogReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(final Context c, Intent intent) {
        final int id = intent.getIntExtra("id", 0);
        final String name = intent.getStringExtra("name") == null ? "That" : intent.getStringExtra("name");
        if (id == 0) return;
        PouchWidget.setLoading(c, true);
        final PendingResult pr = goAsync();
        new Thread(() -> {
            long t0 = System.currentTimeMillis();
            try {
                String msg;
                if (!Store.connected(c)) {
                    msg = "Open the Alberich app once to connect the widget.";
                } else {
                    JSONObject r = Sync.log(c, id);
                    if (r == null) {
                        msg = name + " saved. It will be sent when you are back in range.";
                    } else if (r.length() == 0) {
                        msg = name + " could not be logged. Is it still in the Purse, and is the widget key still valid?";
                    } else {
                        JSONObject tile = r.optJSONObject("tile");
                        int left = tile == null ? 0 : tile.optInt("remaining");
                        String st = r.optString("status");
                        msg = "on_pace".equals(st) ? name + " noted. " + left + " left." :
                              "borrowed".equals(st) ? name + " noted, drawn early. " + left + " left." : name + " noted, but you are over the plan.";
                        Sync.flush(c);
                    }
                    Sync.state(c);
                }
                toast(c, msg);
                PouchWidget.holdSpinner(t0);
                PouchWidget.refreshAll(c);
            } finally {
                pr.finish();
            }
        }).start();
    }

    static void toast(Context c, String text) {
        new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(c, text, Toast.LENGTH_SHORT).show());
    }
}
