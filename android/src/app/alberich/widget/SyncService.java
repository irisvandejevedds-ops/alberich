package app.alberich.widget;

import android.app.job.JobParameters;
import android.app.job.JobService;

/** Runs in the background: syncs with the paired computer when there is one, and repaints the widget (its tiles change as credits unlock). */
public class SyncService extends JobService {
    @Override
    public boolean onStartJob(final JobParameters params) {
        new Thread(() -> {
            try {
                if (Desk.paired(SyncService.this)) Desk.sync(SyncService.this);
            } finally {
                PouchWidget.refreshAll(SyncService.this);
                jobFinished(params, false);
            }
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) { return false; }
}
