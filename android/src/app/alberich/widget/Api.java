package app.alberich.widget;

import android.content.Context;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONException;
import org.json.JSONObject;

final class Api {
    private Api() {}

    static final class HttpError extends IOException {
        final int code;
        HttpError(int code) { super("HTTP " + code); this.code = code; }
    }

    static JSONObject call(Context c, String method, String path, JSONObject body) throws IOException, JSONException {
        return call(c, method, path, body, 15000, 20000);       // a phone's VPN can take a while to wake
    }

    static JSONObject call(Context c, String method, String path, JSONObject body, int connectMs, int readMs) throws IOException, JSONException {
        HttpURLConnection h = (HttpURLConnection) new URL(Store.url(c) + path).openConnection();
        try {
            h.setConnectTimeout(connectMs);
            h.setReadTimeout(readMs);
            h.setRequestMethod(method);
            h.setRequestProperty("Authorization", "Bearer " + Store.token(c));
            h.setRequestProperty("Accept", "application/json");
            if (body != null) {
                h.setDoOutput(true);
                h.setRequestProperty("Content-Type", "application/json");
                byte[] b = body.toString().getBytes("UTF-8");
                try (OutputStream o = h.getOutputStream()) { o.write(b); }
            }
            int code = h.getResponseCode();
            if (code >= 400) throw new HttpError(code);
            try (InputStream in = h.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                return new JSONObject(out.toString("UTF-8"));
            }
        } finally {
            h.disconnect();
        }
    }
}
