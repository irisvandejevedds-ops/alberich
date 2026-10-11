package app.alberich.widget;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private TextView status;
    private EditText url, token;
    private Typeface pixel, script;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Pixel.load(this);
        pixel = Typeface.createFromAsset(getAssets(), "pixel.ttf");
        script = Typeface.createFromAsset(getAssets(), "script.otf");
        getWindow().setStatusBarColor(Color.parseColor("#5B3A82"));
        handle(getIntent());
        if (getIntent().getBooleanExtra("open_web", false) && Store.connected(this)) {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(Store.url(this))));
            finish();
            return;
        }
        build();
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        handle(i);
        refreshStatus();
    }

    private void handle(Intent i) {
        Uri d = i == null ? null : i.getData();
        if (d != null && "alberich".equals(d.getScheme()) && "setup".equals(d.getHost())) {
            String u = d.getQueryParameter("url"), t = d.getQueryParameter("token");
            if (u != null && t != null && u.startsWith("https://") && t.startsWith("alw_")) {
                Store.saveConn(this, u, t);
                new Thread(this::sendAll).start();
                Toast.makeText(this, "Connected. Add the widget from your home screen.", Toast.LENGTH_LONG).show();
            }
        }
    }

    private TextView text(String s, int sp, int color, Typeface f) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setTypeface(f);
        t.setPadding(0, 8, 0, 8);
        return t;
    }

    private Button button(String s, View.OnClickListener l) {
        Button bt = new Button(this);
        bt.setText(s);
        bt.setTypeface(pixel);
        bt.setAllCaps(true);
        bt.setTextColor(Color.parseColor("#2A1B12"));
        bt.setBackgroundColor(Color.parseColor("#E0AC3A"));
        bt.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = 16;
        bt.setLayoutParams(lp);
        return bt;
    }

    private void build() {
        int ink = Color.parseColor("#2A1B12");
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.parseColor("#E8D6A6"));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(36, 80, 36, 60);
        sv.addView(col);

        TextView title = text("Alberich", 44, Color.parseColor("#5B3A82"), script);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        col.addView(title);
        col.addView(text("The home-screen widget", 16, ink, pixel));
        status = text("", 15, ink, pixel);
        col.addView(status);

        col.addView(text("Address", 12, Color.parseColor("#5E4A38"), pixel));
        url = new EditText(this);
        url.setTypeface(pixel);
        url.setSingleLine(true);
        url.setHint("https://your-machine.ts.net:8443");
        url.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        url.setText(Store.url(this));
        col.addView(url);
        col.addView(text("Key", 12, Color.parseColor("#5E4A38"), pixel));
        token = new EditText(this);
        token.setTypeface(pixel);
        token.setSingleLine(true);
        token.setHint("alw_...");
        token.setText(Store.token(this));
        col.addView(token);

        col.addView(button("Save and test", v -> {
            Store.saveConn(this, url.getText().toString(), token.getText().toString());
            new Thread(this::saveAndTest).start();
        }));
        col.addView(button("Send saved taps now", v -> new Thread(this::sendAll).start()));
        col.addView(button("Open Alberich", v -> { if (Store.connected(this)) startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(Store.url(this)))); }));
        col.addView(text("To add the widget: press and hold an empty spot on your home screen, choose Widgets, find Alberich, and drag it out. Stretch it to show more pouches.", 14, ink, pixel));
        setContentView(sv);
        refreshStatus();
    }

    private void note(String msg) {
        runOnUiThread(() -> { if (msg != null) Toast.makeText(this, msg, Toast.LENGTH_LONG).show(); refreshStatus(); });
    }

    private void saveAndTest() {
        boolean reached;
        try { Api.call(this, "GET", "/api/state", null); reached = true; } catch (Exception e) { reached = false; }
        if (reached) { Sync.state(this); PouchWidget.refreshAll(this); }
        note(reached ? "Connected." : "Could not reach Alberich. Is Tailscale on? Is the key right?");
    }

    private void sendAll() {
        Sync.flush(this);
        PouchWidget.refreshAll(this);
        note(null);
    }

    private void refreshStatus() {
        if (status == null) return;
        int n = Store.pending(this);
        status.setText((Store.connected(this) ? "Connected to " + Store.url(this) : "Not connected yet.") + (n > 0 ? "\n" + n + " tap" + (n == 1 ? "" : "s") + " waiting to be sent." : ""));
    }
}
