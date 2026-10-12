package app.alberich.widget;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.View;
import android.widget.RemoteViews;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

public class PouchWidget extends AppWidgetProvider {
    private static final int COLS_MAX = 8, ROWS_MAX = 4;
    private static final int[] SLOT = {R.id.s0, R.id.s1, R.id.s2, R.id.s3, R.id.s4, R.id.s5, R.id.s6, R.id.s7,
        R.id.s8, R.id.s9, R.id.s10, R.id.s11, R.id.s12, R.id.s13, R.id.s14, R.id.s15,
        R.id.s16, R.id.s17, R.id.s18, R.id.s19, R.id.s20, R.id.s21, R.id.s22, R.id.s23,
        R.id.s24, R.id.s25, R.id.s26, R.id.s27, R.id.s28, R.id.s29, R.id.s30, R.id.s31};
    private static final int[] ROW = {R.id.r0, R.id.r1, R.id.r2, R.id.r3};

    @Override
    public void onUpdate(Context c, AppWidgetManager m, int[] ids) {
        paint(c, m, ids);
        Local.ensurePeriodic(c);
    }

    @Override
    public void onEnabled(Context c) { Local.ensurePeriodic(c); }

    @Override
    public void onAppWidgetOptionsChanged(Context c, AppWidgetManager m, int id, Bundle options) {
        paint(c, m, new int[]{id});
    }

    @Override
    public void onReceive(final Context c, Intent intent) {
        super.onReceive(c, intent);
    }

    /** Redraw from what is stored; safe on any thread. */
    static void refreshAll(Context c) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        int[] ids = m.getAppWidgetIds(new ComponentName(c, PouchWidget.class));
        if (ids.length > 0) paint(c, m, ids);
    }

    private static void paint(Context c, AppWidgetManager m, int[] ids) {
        for (int id : ids) m.updateAppWidget(id, build(c, m, id));
    }

    /** Swap the refresh arrow for the spinner (or back) without redrawing anything else. */
    static void setLoading(Context c, boolean on) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        int[] ids = m.getAppWidgetIds(new ComponentName(c, PouchWidget.class));
        if (ids.length == 0) return;
        RemoteViews rv = new RemoteViews(c.getPackageName(), R.layout.widget);
        rv.setViewVisibility(R.id.spin, on ? View.VISIBLE : View.GONE);
        rv.setViewVisibility(R.id.refresh, on ? View.GONE : View.VISIBLE);
        m.partiallyUpdateAppWidget(ids, rv);
    }

    /** Keep the spinner visible long enough to be noticed. */
    static void holdSpinner(long startedAt) {
        long left = 900 - (System.currentTimeMillis() - startedAt);
        if (left > 0) { try { Thread.sleep(left); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }
    }

    static List<JSONObject> tiles(JSONObject state) {
        List<JSONObject> active = new ArrayList<JSONObject>();
        if (state == null) return active;
        JSONArray all = state.optJSONArray("tiles");
        if (all == null) return active;
        for (int i = 0; i < all.length(); i++) {
            JSONObject t = all.optJSONObject(i);
            if (t != null && !"unset".equals(t.optString("status"))) active.add(t);
        }
        if (active.isEmpty()) for (int i = 0; i < all.length(); i++) { JSONObject t = all.optJSONObject(i); if (t != null) active.add(t); }
        return active;
    }

    static RemoteViews build(Context c, AppWidgetManager m, int id) {
        float d = c.getResources().getDisplayMetrics().density;
        Bundle o = m.getAppWidgetOptions(id);
        boolean land = c.getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        int wDp = o.getInt(land ? AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH : AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 280);
        int hDp = o.getInt(land ? AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT : AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 200);
        int W = Math.round(wDp * d), H = Math.round(hDp * d);
        int pad = Math.round(10 * d), hdr = Math.round(40 * d);

        RemoteViews rv = new RemoteViews(c.getPackageName(), R.layout.widget);
        rv.setImageViewBitmap(R.id.bg, Pixel.wall(c, W, H));

        JSONObject state = Local.state(c);
        String mood = state.optString("mood", "neutral");
        Bitmap[] hf = Pixel.headFrames(c, mood, Math.round(38 * d));
        rv.setImageViewBitmap(R.id.h0, hf[0]); rv.setImageViewBitmap(R.id.h1, hf[1]); rv.setImageViewBitmap(R.id.h2, hf[2]); rv.setImageViewBitmap(R.id.h3, hf[3]);
        int titleW = W - 2 * pad - Math.round(48 * d) * 2;
        rv.setImageViewBitmap(R.id.title, Pixel.title(c, titleW, hdr));
        Intent open = new Intent(c, MainActivity.class).putExtra("open_web", true);
        PendingIntent openPi = PendingIntent.getActivity(c, 1, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.title, openPi);
        rv.setOnClickPendingIntent(R.id.headflip, openPi);
        rv.setImageViewBitmap(R.id.refresh, Pixel.refresh(c, Desk.stale(c)));
        rv.setViewVisibility(R.id.refresh, View.VISIBLE);
        rv.setViewVisibility(R.id.spin, View.GONE);
        rv.setOnClickPendingIntent(R.id.refresh, PendingIntent.getBroadcast(c, 2, new Intent(c, RefreshReceiver.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));

        List<JSONObject> list = tiles(state);
        rv.setViewVisibility(R.id.empty, list.isEmpty() ? View.VISIBLE : View.GONE);
        if (list.isEmpty()) rv.setImageViewBitmap(R.id.empty, Pixel.hint(c, "Open Alberich once to set up your pouches.", W));
        int n = Math.min(list.size(), COLS_MAX * ROWS_MAX);

        int cw = W - 2 * pad, ch = H - 2 * pad - hdr;
        int gap = Math.round(3 * d);
        float capW = 62 * d;
        int cols = 1, rows = 1, bestEmpty = 1 << 20;
        float best = -1;
        for (int cc = 1; cc <= Math.min(Math.max(n, 1), COLS_MAX); cc++) {
            int rr = (int) Math.ceil(n / (double) cc);
            if (rr > ROWS_MAX) continue;
            float s = Math.min(Math.min(cw / (float) cc - gap, (ch / (float) rr - gap) / 1.1f), capW);
            int empty = cc * rr - n;
            boolean better = s > best + 1 || (Math.abs(s - best) <= 1 && (empty < bestEmpty || (empty == bestEmpty && rr < rows)));
            if (better) { best = s; cols = cc; rows = rr; bestEmpty = empty; }
        }
        int tw = Math.max(8, Math.round(Math.min(cw / (float) cols - gap, capW)));
        int th = Math.max(8, Math.round(Math.min(ch / (float) rows - gap, tw * 1.13f)));

        for (int r = 0; r < ROWS_MAX; r++) {
            rv.setViewVisibility(ROW[r], r < rows ? View.VISIBLE : View.GONE);
            for (int k = 0; k < COLS_MAX; k++) {
                int slot = SLOT[r * COLS_MAX + k], idx = r * cols + k;
                if (r >= rows || k >= cols) { rv.setViewVisibility(slot, View.GONE); continue; }
                if (idx >= n) { rv.setViewVisibility(slot, View.INVISIBLE); continue; }
                JSONObject t = list.get(idx);
                rv.setViewVisibility(slot, View.VISIBLE);
                rv.setImageViewBitmap(slot, Pixel.tile(c, t.optString("name"), t.optString("icon"), t.optString("status"),
                        t.optInt("remaining"), t.optInt("allotted"), tw, th));
                Intent log = new Intent(c, LogReceiver.class).putExtra("id", t.optString("id")).putExtra("name", t.optString("name"));
                rv.setOnClickPendingIntent(slot, PendingIntent.getBroadcast(c, 1000 + (t.optString("id").hashCode() & 0xFFFFF), log,
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
            }
        }
        return rv;
    }
}
