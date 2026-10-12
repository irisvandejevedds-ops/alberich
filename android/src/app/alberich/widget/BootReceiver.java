package app.alberich.widget;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Alarms do not survive a restart, so plan them again when the phone starts. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent in) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(in.getAction())) {
            Notify.arm(c);
            Local.ensurePeriodic(c);
            PouchWidget.refreshAll(c);
        }
    }
}
