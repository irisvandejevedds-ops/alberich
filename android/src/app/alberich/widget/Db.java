package app.alberich.widget;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** The phone's own copy of the pouches and their logs. The app, the widget, the reminders and the sync all read and write here. */
final class Db extends SQLiteOpenHelper {
    private static Db inst;

    static synchronized Db get(Context c) {
        if (inst == null) inst = new Db(c.getApplicationContext());
        return inst;
    }

    private Db(Context c) { super(c, "alberich.db", null, 1); }

    @Override
    public void onCreate(SQLiteDatabase d) {
        d.execSQL("CREATE TABLE acts(id TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL, phrase TEXT NOT NULL DEFAULT '', estimate_cents INTEGER NOT NULL DEFAULT 0, weight INTEGER NOT NULL DEFAULT 3, "
                + "credits INTEGER NOT NULL DEFAULT 0, cadence TEXT NOT NULL DEFAULT '{}', sort INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, auto_credits INTEGER NOT NULL DEFAULT 0, "
                + "updated_at TEXT NOT NULL, dirty INTEGER NOT NULL DEFAULT 1)");
        d.execSQL("CREATE TABLE logs(id TEXT PRIMARY KEY, act_id TEXT NOT NULL, ts TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'live', note TEXT, undone_at TEXT, updated_at TEXT NOT NULL, dirty INTEGER NOT NULL DEFAULT 1)");
        d.execSQL("CREATE INDEX ix_logs_act ON logs(act_id, ts)");
        d.execSQL("CREATE INDEX ix_logs_dirty ON logs(dirty)");
        d.execSQL("CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT NOT NULL)");
    }

    @Override
    public void onUpgrade(SQLiteDatabase d, int a, int b) {}

    static String nowIso() { return iso(new Date()); }

    static String iso(Date d) {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(d);
    }

    static long parseIso(String s) {
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            return f.parse(s).getTime();
        } catch (Exception e) { return 0; }
    }

    static String uuid() { return UUID.randomUUID().toString(); }

    // ---------- pouches ----------
    private static final String ACOLS = "id, name, icon, phrase, estimate_cents, weight, credits, cadence, sort, archived, auto_credits, updated_at";

    private static JSONObject actRow(Cursor c) throws JSONException {
        JSONObject cad;
        try { cad = new JSONObject(c.getString(7)); } catch (JSONException e) { cad = new JSONObject(); }
        return new JSONObject().put("id", c.getString(0)).put("name", c.getString(1)).put("icon", c.getString(2)).put("phrase", c.getString(3)).put("estimateCents", c.getInt(4))
                .put("weight", c.getInt(5)).put("creditsPerMonth", c.getInt(6)).put("cadence", cad).put("sort", c.getInt(8)).put("archived", c.getInt(9) != 0)
                .put("autoCredits", c.getInt(10) != 0).put("updatedAt", c.getString(11));
    }

    synchronized JSONArray acts(boolean dirtyOnly) {
        JSONArray out = new JSONArray();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT " + ACOLS + " FROM acts" + (dirtyOnly ? " WHERE dirty=1" : "") + " ORDER BY sort, id", null)) {
            while (c.moveToNext()) out.put(actRow(c));
        } catch (JSONException e) { /* skip */ }
        return out;
    }

    private void writeAct(JSONObject a, int dirty) throws JSONException {
        ContentValues v = new ContentValues();
        v.put("id", a.getString("id")); v.put("name", a.getString("name")); v.put("icon", a.optString("icon", "star")); v.put("phrase", a.optString("phrase", ""));
        v.put("estimate_cents", a.optInt("estimateCents", 0)); v.put("weight", a.optInt("weight", 3)); v.put("credits", a.optInt("creditsPerMonth", 0));
        Object cad = a.opt("cadence");
        v.put("cadence", cad == null || cad == JSONObject.NULL ? "{}" : cad.toString());
        v.put("sort", a.optInt("sort", 0)); v.put("archived", a.optBoolean("archived", false) ? 1 : 0); v.put("auto_credits", a.optBoolean("autoCredits", false) ? 1 : 0);
        v.put("updated_at", a.optString("updatedAt", nowIso())); v.put("dirty", dirty);
        getWritableDatabase().insertWithOnConflict("acts", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized void putAct(JSONObject a) throws JSONException { writeAct(a, 1); }

    synchronized boolean putActFromDesk(JSONObject a) throws JSONException {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT updated_at FROM acts WHERE id=?", new String[]{a.getString("id")})) {
            if (c.moveToFirst() && c.getString(0).compareTo(a.optString("updatedAt", "")) >= 0) return false;
        }
        writeAct(a, 0);
        return true;
    }

    // ---------- logs ----------
    private static JSONObject logRow(Cursor c) throws JSONException {
        return new JSONObject().put("id", c.getString(0)).put("activityId", c.getString(1)).put("ts", c.getString(2)).put("source", c.getString(3))
                .put("note", c.isNull(4) ? JSONObject.NULL : c.getString(4)).put("undoneAt", c.isNull(5) ? JSONObject.NULL : c.getString(5)).put("updatedAt", c.getString(6));
    }

    synchronized JSONArray logs(boolean dirtyOnly) {
        JSONArray out = new JSONArray();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT id, act_id, ts, source, note, undone_at, updated_at FROM logs" + (dirtyOnly ? " WHERE dirty=1" : "") + " ORDER BY ts, id", null)) {
            while (c.moveToNext()) out.put(logRow(c));
        } catch (JSONException e) { /* skip */ }
        return out;
    }

    private void writeLog(JSONObject l, int dirty) throws JSONException {
        ContentValues v = new ContentValues();
        v.put("id", l.getString("id")); v.put("act_id", l.getString("activityId")); v.put("ts", l.getString("ts")); v.put("source", l.optString("source", "live").isEmpty() ? "live" : l.optString("source", "live"));
        if (l.isNull("note")) v.putNull("note"); else v.put("note", l.optString("note"));
        if (l.isNull("undoneAt") || l.optString("undoneAt").isEmpty()) v.putNull("undone_at"); else v.put("undone_at", l.getString("undoneAt"));
        v.put("updated_at", l.optString("updatedAt", nowIso())); v.put("dirty", dirty);
        getWritableDatabase().insertWithOnConflict("logs", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized void putLog(JSONObject l) throws JSONException { writeLog(l, 1); }

    synchronized boolean putLogFromDesk(JSONObject l) throws JSONException {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT updated_at FROM logs WHERE id=?", new String[]{l.getString("id")})) {
            if (c.moveToFirst() && c.getString(0).compareTo(l.optString("updatedAt", "")) >= 0) return false;
        }
        writeLog(l, 0);
        return true;
    }

    /** After a successful push: what was sent no longer needs sending, unless it changed again meanwhile. */
    synchronized void markSent(String table, JSONArray sent) {
        SQLiteDatabase d = getWritableDatabase();
        for (int i = 0; i < sent.length(); i++) {
            JSONObject e = sent.optJSONObject(i);
            if (e != null) d.execSQL("UPDATE " + table + " SET dirty=0 WHERE id=? AND updated_at=?", new Object[]{e.optString("id"), e.optString("updatedAt")});
        }
    }

    /** Taps on one pouch since a moment, not undone. */
    synchronized int tapsSince(String actId, String sinceIso) {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM logs WHERE act_id=? AND undone_at IS NULL AND ts>?", new String[]{actId, sinceIso})) { return c.moveToFirst() ? c.getInt(0) : 0; }
    }

    synchronized String actName(String id) {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT name FROM acts WHERE id=?", new String[]{id})) { return c.moveToFirst() ? c.getString(0) : null; }
    }

    // ---------- settings ----------
    synchronized String setting(String k) {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT v FROM settings WHERE k=?", new String[]{k})) { return c.moveToFirst() ? c.getString(0) : null; }
    }

    synchronized void setSetting(String k, String v) {
        ContentValues cv = new ContentValues();
        cv.put("k", k); cv.put("v", v);
        getWritableDatabase().insertWithOnConflict("settings", null, cv, SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized JSONObject settings() {
        JSONObject o = new JSONObject();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT k, v FROM settings", null)) { while (c.moveToNext()) o.put(c.getString(0), c.getString(1)); } catch (JSONException e) { /* skip */ }
        return o;
    }

    synchronized void deleteAll() {
        SQLiteDatabase d = getWritableDatabase();
        d.delete("acts", null, null); d.delete("logs", null, null); d.delete("settings", null, null);
    }
}
