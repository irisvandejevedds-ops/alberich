package app.alberich.widget;

import android.app.AlarmManager;
import android.app.job.JobParameters;
import android.app.job.JobService;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/** Runs in the background: syncs with the paired computer when there is one, and repaints the widget (its tiles change as credits unlock).
 *  When the widget's refresh arrow started it, it also keeps the spinner up for a moment and says so if the computer could not be reached. */
public class SyncService extends JobService {
    @Override
    public boolean onStartJob(final JobParameters params) {
        final boolean asked = params.getJobId() == Local.JOB_REFRESH;
        new Thread(() -> {
            long t0 = System.currentTimeMillis();
            String why = null;
            try {
                if (Desk.paired(SyncService.this)) { String r = Desk.sync(SyncService.this); if (!r.isEmpty()) why = r; }
            } catch (RuntimeException e) {
                why = "something went wrong (" + e.getClass().getSimpleName() + ")";
            } finally {
                if (asked) {
                    PouchWidget.holdSpinner(t0);
                    AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
                    if (am != null) am.cancel(RefreshReceiver.clearIntent(SyncService.this));
                }
                PouchWidget.refreshAll(SyncService.this);
                if (asked && why != null) {
                    final String msg = "Couldn't sync: " + why + ".";
                    new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(SyncService.this, msg, Toast.LENGTH_LONG).show());
                }
                jobFinished(params, false);
            }
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) { return false; }
}
