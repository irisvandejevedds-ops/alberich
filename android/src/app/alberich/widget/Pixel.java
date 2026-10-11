package app.alberich.widget;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.Random;
import org.json.JSONArray;
import org.json.JSONObject;

/** Draws the pixel-art pieces of the widget: stone wall, pouches, Alberich's head, title. Everything is drawn at its final pixel size. */
final class Pixel {
    private Pixel() {}

    private static Typeface pixelFace, scriptFace;
    private static JSONObject icons, sprite;
    private static final Map<String, Integer> pal = new HashMap<String, Integer>();

    static synchronized void load(Context c) {
        if (icons != null) return;
        try {
            pixelFace = Typeface.createFromAsset(c.getAssets(), "pixel.ttf");
            scriptFace = Typeface.createFromAsset(c.getAssets(), "script.otf");
            JSONObject all = readJson(c, "icons.json");
            JSONObject p = all.getJSONObject("pal");
            java.util.Iterator<String> it = p.keys();
            while (it.hasNext()) { String k = it.next(); pal.put(k, Color.parseColor(p.getString(k))); }
            sprite = readJson(c, "alberich.json");
            icons = all.getJSONObject("icons");
        } catch (Exception e) {
            icons = new JSONObject();
            sprite = new JSONObject();
        }
    }

    private static JSONObject readJson(Context c, String name) throws Exception {
        InputStream in = c.getAssets().open(name);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] b = new byte[4096];
        int n;
        while ((n = in.read(b)) > 0) out.write(b, 0, n);
        in.close();
        return new JSONObject(out.toString("UTF-8"));
    }

    private static Paint flat() {
        Paint p = new Paint();
        p.setAntiAlias(false);
        p.setFilterBitmap(false);
        return p;
    }

    static void drawIcon(Canvas cv, String name, float x, float y, float cell) {
        JSONArray rows = icons.optJSONArray(name);
        if (rows == null) rows = icons.optJSONArray("star");
        if (rows == null) return;
        Paint p = flat();
        for (int r = 0; r < rows.length(); r++) {
            String row = rows.optString(r);
            for (int col = 0; col < row.length(); col++) {
                Integer color = pal.get(String.valueOf(row.charAt(col)));
                if (color == null) continue;
                p.setColor(color);
                cv.drawRect(x + col * cell, y + r * cell, x + (col + 1) * cell, y + (r + 1) * cell, p);
            }
        }
    }

    /** A rectangle whose corners step in two pixels, like the borders in the app. */
    static Path stepped(float l, float t, float r, float b, float n) {
        Path p = new Path();
        p.moveTo(l, t + 2 * n); p.lineTo(l + n, t + 2 * n); p.lineTo(l + n, t + n); p.lineTo(l + 2 * n, t + n); p.lineTo(l + 2 * n, t);
        p.lineTo(r - 2 * n, t); p.lineTo(r - 2 * n, t + n); p.lineTo(r - n, t + n); p.lineTo(r - n, t + 2 * n); p.lineTo(r, t + 2 * n);
        p.lineTo(r, b - 2 * n); p.lineTo(r - n, b - 2 * n); p.lineTo(r - n, b - n); p.lineTo(r - 2 * n, b - n); p.lineTo(r - 2 * n, b);
        p.lineTo(l + 2 * n, b); p.lineTo(l + 2 * n, b - n); p.lineTo(l + n, b - n); p.lineTo(l + n, b - 2 * n); p.lineTo(l, b - 2 * n);
        p.close();
        return p;
    }

    /** The radius the launcher clips every widget to (Android 12+); our frame has to follow it or its corners are cut off. */
    static float systemRadius(Context c) {
        float d = c.getResources().getDisplayMetrics().density;
        try {
            int id = android.content.res.Resources.getSystem().getIdentifier("system_app_widget_background_radius", "dimen", "android");
            if (id != 0) {
                float r = android.content.res.Resources.getSystem().getDimension(id);
                if (r > 0) return r;
            }
        } catch (RuntimeException e) { /* fall through */ }
        return 28 * d;
    }

    /** Cells (each u pixels square) inside a rectangle whose corners are quarter circles of radius radCells. */
    static boolean[][] shape(int cols, int rows, double radCells) {
        boolean[][] g = new boolean[rows][cols];
        for (int y = 0; y < rows; y++) {
            for (int x = 0; x < cols; x++) {
                double cx = Math.min(x + 0.5, cols - x - 0.5), cy = Math.min(y + 0.5, rows - y - 0.5);
                boolean in = true;
                if (cx < radCells && cy < radCells) in = Math.hypot(radCells - cx, radCells - cy) <= radCells;
                g[y][x] = in;
            }
        }
        return g;
    }

    /** One cell thinner on every side, diagonals included, so a border stays one cell on the straights and a connected stair on the curve. */
    static boolean[][] erode(boolean[][] g) {
        int rows = g.length, cols = g[0].length;
        boolean[][] n = new boolean[rows][cols];
        for (int y = 0; y < rows; y++)
            for (int x = 0; x < cols; x++)
                n[y][x] = g[y][x] && y > 0 && y < rows - 1 && x > 0 && x < cols - 1 && g[y - 1][x] && g[y + 1][x] && g[y][x - 1] && g[y][x + 1]
                    && g[y - 1][x - 1] && g[y - 1][x + 1] && g[y + 1][x - 1] && g[y + 1][x + 1];
        return n;
    }

    static Path cells(boolean[][] g, int u, int w, int h) {
        Path p = new Path();
        for (int y = 0; y < g.length; y++) {
            int x = 0;
            while (x < g[y].length) {
                if (!g[y][x]) { x++; continue; }
                int x0 = x;
                while (x < g[y].length && g[y][x]) x++;
                p.addRect(x0 * u, y * u, Math.min(w, x * u), Math.min(h, (y + 1) * u), Path.Direction.CW);
            }
        }
        return p;
    }

    /** The widget's background: ashlar stone in a gilded frame whose corners step like the launcher's own rounding. */
    static Bitmap wall(Context c, int w, int h) {
        float d = c.getResources().getDisplayMetrics().density;
        int u = Math.max(2, Math.round(1.5f * d));
        Bitmap bm = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bm);
        Paint p = flat();
        int cols = (w + u - 1) / u, rows = (h + u - 1) / u;
        boolean[][] outer = shape(cols, rows, systemRadius(c) / u), gold = erode(outer), dark = erode(gold), face = erode(dark);
        p.setColor(Color.parseColor("#14151C")); cv.drawPath(cells(outer, u, w, h), p);
        p.setColor(Color.parseColor("#E0AC3A")); cv.drawPath(cells(gold, u, w, h), p);
        p.setColor(Color.parseColor("#14151C")); cv.drawPath(cells(dark, u, w, h), p);
        cv.save();
        cv.clipPath(cells(face, u, w, h));
        int[][] bases = {{0x43, 0x48, 0x59}, {0x3E, 0x43, 0x55}, {0x48, 0x4D, 0x5F}, {0x3A, 0x3F, 0x51}};
        Random rnd = new Random(7);
        int bh = 9 * u, mortar = u;
        int row = 0;
        for (int y = 3 * u; y < h - 3 * u; y += bh, row++) {
            int x = 3 * u - ((row % 2) * (8 + rnd.nextInt(6)) * u);
            while (x < w - 3 * u) {
                int bw = (11 + rnd.nextInt(10)) * u;
                int[] b = bases[rnd.nextInt(bases.length)];
                p.setColor(Color.rgb(b[0], b[1], b[2]));
                cv.drawRect(x, y, x + bw, y + bh, p);
                p.setColor(Color.rgb(b[0] + 20, b[1] + 20, b[2] + 24));
                cv.drawRect(x, y, x + bw, y + u, p);
                cv.drawRect(x, y, x + u, y + bh, p);
                p.setColor(Color.rgb(0x26, 0x28, 0x33));
                cv.drawRect(x, y + bh - mortar, x + bw, y + bh, p);
                cv.drawRect(x + bw - mortar, y, x + bw, y + bh, p);
                for (int k = rnd.nextInt(4); k > 0; k--) {
                    int sx = x + 2 * u + rnd.nextInt(Math.max(1, bw / u - 5)) * u, sy = y + 2 * u + rnd.nextInt(5) * u;
                    p.setColor(rnd.nextBoolean() ? Color.argb(90, 20, 20, 30) : Color.argb(70, 255, 255, 255));
                    cv.drawRect(sx, sy, sx + u, sy + u, p);
                }
                x += bw;
            }
        }
        cv.restore();
        return bm;
    }

    static Bitmap tile(Context c, String name, String icon, String status, int remaining, int allotted, int w, int h) {
        load(c);
        float d = c.getResources().getDisplayMetrics().density;
        int u = Math.max(2, Math.round((w < 50 * d ? 1f : 1.5f) * d));
        Bitmap bm = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bm);
        int fill = Color.parseColor("#F6ECCF"), edge = Color.parseColor("#2A1B12"), shadow = Color.parseColor("#6B5A40"), gem = Color.parseColor("#9E9684");
        if ("available".equals(status)) { fill = Color.parseColor("#CFEBD4"); edge = Color.parseColor("#1F5E36"); shadow = edge; gem = Color.parseColor("#47B26B"); }
        else if ("overdrawn".equals(status)) { fill = Color.parseColor("#F6D2CC"); edge = Color.parseColor("#7E2018"); shadow = edge; gem = Color.parseColor("#D8453B"); }
        else if ("waiting".equals(status)) gem = Color.parseColor("#4A8FE0");
        boolean dim = "done".equals(status) || "unset".equals(status);
        Paint p = flat();
        float body = h - u;
        p.setColor(shadow); cv.drawPath(stepped(0, u, w, h, u), p);
        p.setColor(edge); cv.drawPath(stepped(0, 0, w, body, u), p);
        p.setColor(Color.WHITE); cv.drawPath(stepped(u, u, w - u, body - u, u), p);
        p.setColor(fill); cv.drawPath(stepped(2 * u, 2 * u, w - 2 * u, body - 2 * u, u), p);

        boolean showName = w >= 46 * d;
        float ts = Math.max(7 * d, Math.min(w * 0.17f, 11 * d));
        Paint t = flat();
        t.setTypeface(pixelFace);
        t.setColor(Color.parseColor("#2A1B12"));
        t.setTextSize(ts);
        t.setTextAlign(Paint.Align.CENTER);
        String[] lines = showName ? wrap(t, name, w - 6 * u) : new String[0];
        int nl = Math.min(2, lines.length);
        float lineH = ts * 1.18f;
        float cntSize = Math.max(7 * d, ts * 0.8f);
        float topPad = 2 * u + Math.max(3 * u, cntSize * 0.95f) + u / 2f;
        float avail = body - topPad - 2 * u;
        float iconTarget = Math.min(w - 8 * u, avail - nl * lineH - (nl > 0 ? u : 0));
        int cell = Math.max(1, (int) (iconTarget / 16f));
        float iconPx = 16 * cell;
        float block = iconPx + (nl > 0 ? u + nl * lineH : 0);
        float top = topPad + Math.max(0, (avail - block) / 2f);
        drawIcon(cv, icon, (w - iconPx) / 2f, top, cell);
        for (int i = 0; i < nl; i++) cv.drawText(lines[i], w / 2f, top + iconPx + u + (i + 0.85f) * lineH, t);

        Paint cnt = flat();
        cnt.setTypeface(pixelFace);
        cnt.setTextSize(cntSize);
        cnt.setTextAlign(Paint.Align.RIGHT);
        cnt.setColor(Color.parseColor("#5B3A82"));
        cv.drawText(w >= 52 * d ? remaining + "/" + allotted : String.valueOf(remaining), w - 3 * u, 2 * u + cnt.getTextSize() * 0.85f, cnt);
        int g = 3 * u;
        p.setColor(Color.parseColor("#2A1B12")); cv.drawRect(2 * u, 2 * u, 2 * u + g, 2 * u + g, p);
        p.setColor(gem); cv.drawRect(2 * u + u / 2f, 2 * u + u / 2f, 2 * u + g - u / 2f, 2 * u + g - u / 2f, p);
        if (!dim) return bm;
        Bitmap faded = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Paint a = new Paint();
        a.setAlpha(150);
        new Canvas(faded).drawBitmap(bm, 0, 0, a);
        return faded;
    }

    private static String[] wrap(Paint t, String s, float width) {
        String[] words = s.split(" ");
        java.util.ArrayList<String> out = new java.util.ArrayList<String>();
        String cur = "";
        for (String wd : words) {
            String tryS = cur.isEmpty() ? wd : cur + " " + wd;
            if (t.measureText(tryS) <= width || cur.isEmpty()) cur = tryS; else { out.add(cur); cur = wd; }
        }
        if (!cur.isEmpty()) out.add(cur);
        if (out.size() > 2) { String last = out.get(1) + "..."; out.set(1, last); out = new java.util.ArrayList<String>(out.subList(0, 2)); }
        return out.toArray(new String[0]);
    }

    /** Alberich's head and the top of his beard, floating, with this mood's face. */
    static Bitmap head(Context c, String mood, int heightPx) {
        load(c);
        final int ox = 3, oy = 4, bw = 25, cols = bw + 2 * ox, rowsN = 24;
        if (mood == null || sprite.optJSONObject("palettes") == null || sprite.optJSONObject("palettes").optJSONObject(mood) == null) mood = "neutral";
        Map<Integer, String> cells = new HashMap<Integer, String>();
        try {
            JSONArray base = sprite.getJSONArray("base");
            for (int y = 0; y < 22 && y < base.length(); y++) {
                String row = base.getString(y);
                for (int x = 0; x < row.length(); x++) {
                    char ch = row.charAt(x);
                    if (ch == '.') continue;
                    if (y > 16 && Math.abs(x - 12.5) > 8 - (y - 17) * 2) continue;       // beard tapers to a point
                    cells.put((y + oy) * 100 + x + ox, String.valueOf(ch));
                }
            }
            JSONArray ov = sprite.getJSONObject("overlays").getJSONArray(mood);
            for (int i = 0; i < ov.length(); i++) {
                JSONArray o = ov.getJSONArray(i);
                int x = o.getInt(0) + ox, y = o.getInt(1) + oy;
                if (o.getInt(1) > 17 || x < 0 || x >= cols || y < 0 || y >= rowsN) continue;
                cells.put(y * 100 + x, o.getString(2));
            }
            JSONObject colors = sprite.getJSONObject("palettes").getJSONObject(mood);
            int cell = Math.max(1, heightPx / rowsN);
            Bitmap bm = Bitmap.createBitmap(cols * cell, rowsN * cell, Bitmap.Config.ARGB_8888);
            Canvas cv = new Canvas(bm);
            Paint p = flat();
            for (Map.Entry<Integer, String> e : cells.entrySet()) {
                String hex = colors.optString(e.getValue(), "");
                if (hex.isEmpty()) continue;
                p.setColor(Color.parseColor(hex));
                int y = e.getKey() / 100, x = e.getKey() % 100;
                cv.drawRect(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell, p);
            }
            return bm;
        } catch (Exception ex) {
            return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888);
        }
    }

    /** Four frames of the floating head, bobbing one pixel up and down. */
    static Bitmap[] headFrames(Context c, String mood, int heightPx) {
        Bitmap hb = head(c, mood, heightPx);
        int cell = Math.max(1, hb.getHeight() / 24);
        int[] off = {0, -1, 0, 1};
        Bitmap[] out = new Bitmap[4];
        Paint pt = new Paint();
        pt.setFilterBitmap(false);
        for (int i = 0; i < 4; i++) {
            out[i] = Bitmap.createBitmap(hb.getWidth(), hb.getHeight() + 2 * cell, Bitmap.Config.ARGB_8888);
            new Canvas(out[i]).drawBitmap(hb, 0, cell + off[i] * cell, pt);
        }
        return out;
    }

    /** "Alberich" in the script face, left-aligned in a bitmap of exactly the space it has. */
    static Bitmap title(Context c, int w, int h) {
        load(c);
        float d = c.getResources().getDisplayMetrics().density;
        Paint t = new Paint();
        t.setAntiAlias(true);
        t.setTypeface(scriptFace);
        t.setTextSize(Math.min(26 * d, h * 0.82f));
        t.setColor(Color.parseColor("#FFE08A"));
        t.setShadowLayer(0.1f, 2 * d, 2 * d, Color.parseColor("#14151C"));
        Bitmap bm = Bitmap.createBitmap(Math.max(1, w), Math.max(1, h), Bitmap.Config.ARGB_8888);
        new Canvas(bm).drawText("Alberich", Math.max(0, (w - t.measureText("Alberich")) / 2f), h - h * 0.25f, t);
        return bm;
    }

    private static final String[] REFRESH = {
        "..kkkk..", ".k....k.", "k..kkkkk", "k...kkk.", "k....k..", "k.......", ".k....k.", "..kkkk.."};

    static Bitmap refresh(Context c, boolean pending) {
        float d = c.getResources().getDisplayMetrics().density;
        int cell = Math.max(2, Math.round(2 * d));
        Bitmap bm = Bitmap.createBitmap(8 * cell, 8 * cell, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bm);
        Paint p = flat();
        p.setColor(pending ? Color.parseColor("#FFB04A") : Color.parseColor("#FFE08A"));
        for (int r = 0; r < 8; r++) for (int col = 0; col < 8; col++)
            if (REFRESH[r].charAt(col) == 'k') cv.drawRect(col * cell, r * cell, (col + 1) * cell, (r + 1) * cell, p);
        return bm;
    }

    static Bitmap hint(Context c, String text, int w) {
        load(c);
        float d = c.getResources().getDisplayMetrics().density;
        Paint t = flat();
        t.setTypeface(pixelFace);
        t.setTextSize(12 * d);
        t.setColor(Color.parseColor("#F4EBD6"));
        t.setTextAlign(Paint.Align.CENTER);
        String[] lines = wrap(t, text, w - 24 * d);
        Bitmap bm = Bitmap.createBitmap(Math.max(1, w), (int) (lines.length * 16 * d + 8 * d), Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bm);
        for (int i = 0; i < lines.length; i++) cv.drawText(lines[i], w / 2f, (i + 1) * 16 * d, t);
        return bm;
    }
}
