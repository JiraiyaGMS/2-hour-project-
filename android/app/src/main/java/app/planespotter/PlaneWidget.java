package app.planespotter;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import android.view.View;
import android.webkit.CookieManager;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * The home-screen widget: the next plane over your home with a countdown that ticks by itself, where to
 * look, and the last one over. It reads your planespotter (signed in, through the app's sign-in) about every
 * half hour (Android decides exactly when), and whenever you open the app. Tap it to open the app.
 */
public class PlaneWidget extends AppWidgetProvider {
    private static final String[] WORDS = {"north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"};

    static void refreshAll(Context c) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        int[] ids = m.getAppWidgetIds(new ComponentName(c, PlaneWidget.class));
        if (ids.length == 0) return;
        Intent i = new Intent(c, PlaneWidget.class).setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE);
        i.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids);
        c.sendBroadcast(i);
    }

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        final PendingResult done = goAsync();
        final Context c = context.getApplicationContext();
        // Your sign-in, read here on the main thread (Android's cookie store is happiest there).
        String base = MainActivity.base(c);
        String cookie = null;
        try {
            cookie = base.isEmpty() ? null : CookieManager.getInstance().getCookie(base);
        } catch (Exception e) {
            cookie = null;
        }
        final String signIn = cookie;
        new Thread(() -> {
            try {
                RemoteViews v = build(c, signIn);
                for (int id : ids) manager.updateAppWidget(id, v);
            } finally {
                done.finish();
            }
        }).start();
    }

    private static RemoteViews build(Context c, String cookie) {
        RemoteViews v = new RemoteViews(c.getPackageName(), R.layout.widget);
        Intent open = new Intent(c, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        v.setOnClickPendingIntent(R.id.widget, PendingIntent.getActivity(c, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        v.setViewVisibility(R.id.countdown, View.GONE);
        String base = MainActivity.base(c);
        if (base.isEmpty()) return say(v, "✈ planespotter", "Open the app", "Tap here, then type your planespotter's address.", "");
        try {
            HttpURLConnection http = (HttpURLConnection) new URL(base + "/api/next").openConnection();
            http.setConnectTimeout(12000);
            http.setReadTimeout(15000);
            http.setRequestProperty("Accept", "application/json");
            if (cookie != null) http.setRequestProperty("Cookie", cookie);
            int code = http.getResponseCode();
            if (code == 401 || code == 303) return say(v, "✈ planespotter", "Sign in", "Tap here and sign in: then your planes show here.", "");
            if (code != 200) return say(v, "✈ planespotter", "Can't get the planes", "planespotter answered " + code + ". It'll try again soon.", "");
            JSONObject d = new JSONObject(read(http.getInputStream()));
            long now = System.currentTimeMillis() / 1000;
            String head = "✈ " + d.optString("home", "planespotter");
            JSONArray next = d.optJSONArray("next");
            JSONObject p = null;
            for (int i = 0; next != null && i < next.length(); i++) {
                JSONObject q = next.getJSONObject(i);
                if (q.optLong("at") > now - 60) { p = q; break; }
            }
            String foot = "";
            JSONObject last = d.optJSONObject("last");
            if (last != null) {
                long ago = Math.max(0, (now - last.optLong("at")) / 60);
                foot = "Last over: " + last.optString("label") + " · " + (ago < 1 ? "just now" : ago < 120 ? ago + " min ago" : (ago / 60) + " h ago");
            }
            if (d.has("hour_count")) foot += (foot.isEmpty() ? "" : " · ") + d.optInt("hour_count") + " in the last hour";
            if (p == null) return say(v, head, "Nothing due right now", "Planes show up here a few minutes before they come over.", foot);
            String sub = (p.optString("airline").isEmpty() ? "" : p.optString("airline") + " · ")
                    + (p.optString("route").isEmpty() ? "" : p.optString("route") + " · ")
                    + "look " + word(p.optString("direction")) + (p.has("up") ? ", " + p.optInt("up") + "° up" : "");
            say(v, head, p.optString("label"), sub, foot);
            long secs = p.optLong("at") - now;
            if (secs > 0) {  // a countdown that ticks on the home screen by itself
                v.setViewVisibility(R.id.countdown, View.VISIBLE);
                v.setChronometer(R.id.countdown, SystemClock.elapsedRealtime() + secs * 1000, null, true);
                v.setChronometerCountDown(R.id.countdown, true);
            }
            return v;
        } catch (Exception e) {
            return say(v, "✈ planespotter", "Can't get the planes", "No internet? It'll try again soon.", "");
        }
    }

    private static String word(String dir) {
        String[] codes = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
        for (int i = 0; i < codes.length; i++) if (codes[i].equals(dir)) return WORDS[i];
        return dir;
    }

    private static RemoteViews say(RemoteViews v, String head, String label, String sub, String foot) {
        v.setTextViewText(R.id.head, head);
        v.setTextViewText(R.id.label, label);
        v.setTextViewText(R.id.sub, sub);
        v.setTextViewText(R.id.foot, foot);
        return v;
    }

    private static String read(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0 && out.size() < 200_000) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }
}
