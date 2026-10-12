package app.alberich.widget;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/** An invisible screen the refresh arrow opens. It repaints the widget and, if a computer is paired, syncs with it while the app is in the foreground.
 *  It always clears the spinner and closes itself, however it ends. */
public class RefreshActivity extends Activity {
    private boolean done = false;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        final Handler ui = new Handler(Looper.getMainLooper());
        PouchWidget.setLoading(this, true);
        ui.postDelayed(() -> finishUp(null), 45000);          // never spin longer than this
        new Thread(() -> {
            long t0 = System.currentTimeMillis();
            String why = null;
            try {
                if (Desk.paired(this)) { String r = Desk.sync(this); if (!r.isEmpty()) why = r; }
            } catch (RuntimeException e) {
                why = "something went wrong (" + e.getClass().getSimpleName() + ")";
            }
            PouchWidget.holdSpinner(t0);
            final String msg = why;
            ui.post(() -> finishUp(msg));
        }).start();
    }

    private void finishUp(String why) {
        if (done) return;
        done = true;
        PouchWidget.refreshAll(this);                          // repaints from what is saved and puts the arrow back
        if (why != null) Toast.makeText(this, "Couldn't sync: " + why + ".", Toast.LENGTH_LONG).show();
        finish();
        overridePendingTransition(0, 0);
    }
}
