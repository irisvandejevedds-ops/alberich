package app.alberich.widget;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/** Refreshes the widget from the server as a proper background job, so it still gets network when the phone is idle or the app is not in use. */
public class SyncService extends JobService {
    @Override
    public boolean onStartJob(final JobParameters params) {
        final boolean manual = params.getExtras().getInt("manual", 0) == 1;
        new Thread(() -> {
            long t0 = System.currentTimeMillis();
            if (manual) PouchWidget.setLoading(SyncService.this, true);
            try {
                Sync.flush(SyncService.this);
                Sync.state(SyncService.this);
            } finally {
                if (manual) PouchWidget.holdSpinner(t0);
                PouchWidget.refreshAll(SyncService.this);
                final String why = Sync.lastError;
                if (manual && why != null) new Handler(Looper.getMainLooper()).post(() ->
                    Toast.makeText(SyncService.this, "Couldn't reach Alberich: " + why + ". Showing the saved pouches.", Toast.LENGTH_LONG).show());
                jobFinished(params, false);
            }
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) { return false; }
}
