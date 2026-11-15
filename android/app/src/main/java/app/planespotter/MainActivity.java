package app.planespotter;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.speech.tts.TextToSpeech;
import android.text.InputType;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.util.Locale;

/**
 * planespotter for Android: your planespotter's website in an app (its address is asked once), with the
 * phone's own voice for "What's that plane?", the compass for Point, and your location for setting your home.
 * Long-press shortcuts arrive as planespotter:/?say=next and the like.
 */
public class MainActivity extends Activity {
    static final String PREFS = "planespotter";
    static final String KEY_BASE = "base";
    private static final int ASK_LOCATION = 1;
    private static final int PICK_FILE = 2;
    // Point opens a page from planespotter's own files there (it needs the compass, which Android allows).
    private static final String POINT_HOST = "raw.githack.com";

    private WebView web;
    private TextToSpeech voice;
    private boolean voiceReady;
    private String pendingLocationOrigin;
    private GeolocationPermissions.Callback pendingLocation;
    private ValueCallback<Uri[]> pendingFiles;

    static String base(Context c) {
        return c.getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_BASE, "");
    }

    /** "whoflewoverme.com", "https://x.com/" -> "https://x.com" (https only). "" if it isn't an address. */
    static String normalise(String typed) {
        String t = typed == null ? "" : typed.trim();
        if (t.isEmpty()) return "";
        if (t.startsWith("http://")) t = t.substring(7);
        if (t.startsWith("https://")) t = t.substring(8);
        while (t.endsWith("/")) t = t.substring(0, t.length() - 1);
        if (t.isEmpty() || t.contains(" ") || !t.contains(".")) return "";
        return "https://" + t;
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#0B0F14"));
        w.setNavigationBarColor(Color.parseColor("#0B0F14"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0B0F14"));
        // Kept clear of the clock and the navigation bar (so planespotter's own top buttons aren't under them).
        FrameLayout frame = new FrameLayout(this);
        frame.setFitsSystemWindows(true);
        frame.setBackgroundColor(Color.parseColor("#0B0F14"));
        frame.addView(web, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(frame);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        voice = new TextToSpeech(this, status -> {
            voiceReady = status == TextToSpeech.SUCCESS;
            if (voiceReady) voice.setLanguage(Locale.getDefault());
        });
        // The pages speak through this (Android's web view has no voice of its own): window.PlanespotterAndroid.
        web.addJavascriptInterface(new Bridge(), "PlanespotterAndroid");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String host = u.getHost() == null ? "" : u.getHost();
                if (("https".equals(u.getScheme()) && (host.equals(Uri.parse(base(MainActivity.this)).getHost())
                        || host.equals(POINT_HOST)))) {
                    return false;  // your planespotter and its Point page: here
                }
                openOutside(u);  // anything else (adsb.lol, a photo's source...): in your browser
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showProblem(String.valueOf(error.getDescription()));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();  // stay signed in (and let the widget use it)
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                if (hasLocation()) {
                    callback.invoke(origin, true, false);
                } else {
                    pendingLocationOrigin = origin;
                    pendingLocation = callback;
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION}, ASK_LOCATION);
                }
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> files, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = files;
                try {
                    startActivityForResult(params.createIntent(), PICK_FILE);
                } catch (Exception e) {
                    pendingFiles = null;
                    return false;
                }
                return true;
            }
        });

        // Downloads (the noise log, a share card): with your sign-in, into Downloads.
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, URLUtil.guessFileName(url, disposition, mime));
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, "Downloading…", Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                openOutside(Uri.parse(url));
            }
        });

        if (base(this).isEmpty()) askForAddress(getIntent());
        else open(getIntent());
    }

    /** Where to go: a shortcut's page (planespotter:/?say=next) or the app's front page. */
    private void open(Intent intent) {
        String path = "/";
        Uri data = intent == null ? null : intent.getData();
        if (data != null && "planespotter".equals(data.getScheme())) {
            String q = data.getEncodedQuery(), f = data.getEncodedFragment();
            path = "/" + (q != null ? "?" + q : "") + (f != null ? "#" + f : "");
        }
        web.loadUrl(base(this) + path);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (!base(this).isEmpty() && intent.getData() != null) open(intent);
    }

    private void askForAddress(Intent then) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("yourdomain.com");
        input.setText(base(this).replace("https://", ""));
        input.setSingleLine(true);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        input.setPadding(pad, pad / 2, pad, pad / 2);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Your planespotter's address")
                .setMessage("The address your friend sent you, like whoever's-domain.com (no need for https://).")
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("Open", null)
                .create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String b = normalise(input.getText().toString());
            if (b.isEmpty()) {
                input.setError("Type the address, like yourdomain.com");
                return;
            }
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_BASE, b).apply();
            dialog.dismiss();
            open(then);
        }));
        dialog.show();
    }

    private void showProblem(String why) {
        String html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<body style='margin:0;font:17px sans-serif;background:#0b0f14;color:#e8eef5;display:grid;place-items:center;"
                + "height:100vh;text-align:center;padding:24px;box-sizing:border-box'><div><div style='font-size:44px'>✈︎</div>"
                + "<h2>Can't reach planespotter</h2><p style='color:#9aa7b6'>No internet, or the address isn't right ("
                + android.text.Html.escapeHtml(base(this).replace("https://", "")) + ").</p>"
                + "<p><button onclick='location.href=\"" + base(this) + "\"' style='font:inherit;padding:10px 18px;border-radius:10px;"
                + "border:0;background:#e8860c;color:#111;font-weight:700'>Try again</button></p>"
                + "<p><button onclick='PlanespotterAndroid.changeAddress()' style='font:inherit;padding:8px 14px;border-radius:10px;"
                + "border:1px solid #263140;background:none;color:#e8eef5'>Change the address</button></p></div>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private void openOutside(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (Exception e) {
            Toast.makeText(this, "Nothing on this phone opens that link", Toast.LENGTH_SHORT).show();
        }
    }

    private boolean hasLocation() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        if (code == ASK_LOCATION && pendingLocation != null) {
            pendingLocation.invoke(pendingLocationOrigin, hasLocation(), false);
            pendingLocation = null;
        }
    }

    @Override
    protected void onActivityResult(int code, int result, Intent data) {
        if (code == PICK_FILE && pendingFiles != null) {
            pendingFiles.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            pendingFiles = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        PlaneWidget.refreshAll(this);  // (you may have just signed in: the widget can show your planes now)
    }

    @Override
    protected void onPause() {
        web.onPause();
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (voice != null) voice.shutdown();
        web.destroy();
        super.onDestroy();
    }

    /** What the pages may ask of the app: speak, stop speaking, change the address. Nothing else. */
    private class Bridge {
        @JavascriptInterface
        public void speak(String text) {
            if (voiceReady && text != null && !text.trim().isEmpty()) {
                voice.speak(text.length() > 600 ? text.substring(0, 600) : text, TextToSpeech.QUEUE_FLUSH, null, "planespotter");
            }
        }

        @JavascriptInterface
        public void stop() {
            if (voiceReady) voice.stop();
        }

        @JavascriptInterface
        public void changeAddress() {
            runOnUiThread(() -> askForAddress(null));
        }
    }
}
