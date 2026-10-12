package app.alberich.widget;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;

/** The launcher icon. It has no screen of its own: it opens the app. If it was opened by a pairing link, it asks first, because a link can come from anywhere. */
public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Local.ensurePeriodic(this);
        Uri d = getIntent() == null ? null : getIntent().getData();
        if (d == null) { openApp(null); return; }
        final String link = d.toString();
        String who = Desk.describe(link);
        if (who == null) { Toast.makeText(this, "That is not a valid pairing link.", Toast.LENGTH_LONG).show(); finish(); return; }
        new AlertDialog.Builder(this, android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("Pair with " + who + "?")
                .setMessage("Your pouches and taps will be copied to that computer, and what it works out will be copied here. Only continue if you made this code yourself, on your own computer.")
                .setCancelable(false)
                .setPositiveButton("Pair", (dlg, w) -> {
                    String why = Desk.pair(this, link);
                    if (why.isEmpty()) {
                        final android.content.Context app = getApplicationContext();
                        new Thread(() -> Desk.sync(app)).start();
                        Toast.makeText(this, "Paired. Syncing now.", Toast.LENGTH_LONG).show();
                    } else Toast.makeText(this, why, Toast.LENGTH_LONG).show();
                    openApp("#/settings");
                })
                .setNegativeButton("Cancel", (dlg, w) -> finish())
                .show();
    }

    private void openApp(String hash) {
        Intent open = new Intent(this, WebActivity.class);
        if (hash != null) open.putExtra("hash", hash);
        startActivity(open);
        finish();
        overridePendingTransition(0, 0);
    }
}
