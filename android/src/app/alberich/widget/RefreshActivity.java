package app.alberich.widget;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/** An invisible screen the refresh arrow opens. While it is up the app is in the foreground, so it has full network even when the phone is idle.
 *  It always clears the spinner and closes itself, however the fetch ends. */
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
                Sync.flush(this);
                Sync.state(this, true);
                why = Sync.lastError;
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
        if (why != null) Toast.makeText(this, "Couldn't reach Alberich: " + why + ". Showing the saved pouches.", Toast.LENGTH_LONG).show();
        finish();
        overridePendingTransition(0, 0);
    }
}
