package app.alberich.widget;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The widget's refresh arrow. Repaints at once and, if a computer is paired, hands the sync to a background job, so no screen ever opens. */
public class RefreshReceiver extends BroadcastReceiver {
    static final String CLEAR = "app.alberich.widget.CLEAR_SPINNER";

    static PendingIntent clearIntent(Context c) {
        return PendingIntent.getBroadcast(c, 3, new Intent(c, RefreshReceiver.class).setAction(CLEAR), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    @Override
    public void onReceive(Context c, Intent intent) {
        if (CLEAR.equals(intent.getAction()) || !Desk.paired(c)) { PouchWidget.refreshAll(c); return; }
        Local.refreshNow(c);
    }
}
