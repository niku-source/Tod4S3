package com.tod4s3.sync;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.graphics.Color;
import android.graphics.Typeface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayOutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

public final class MainActivity extends Activity {
    // Se puede cambiar por el dominio final sin tocar el token de ningún usuario.
    private static final String API_BASE = "https://tod4s3-production.up.railway.app";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable poller = new Runnable() {
        @Override public void run() {
            refreshDeliveries();
            handler.postDelayed(this, 15_000L);
        }
    };
    private android.content.SharedPreferences prefs;
    private EditText codeInput;
    private TextView status;
    private LinearLayout deliveriesBox;
    private String token;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("tod4s3_sync", MODE_PRIVATE);
        token = prefs.getString("token", "");
        buildUi();
        handler.post(poller);
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 28, 32, 28);
        root.setBackgroundColor(Color.rgb(7, 17, 31));
        scroll.addView(root);

        TextView title = text("Tod4S3 Sync", 28, true);
        root.addView(title);
        TextView intro = text("Descarga tus archivos de ToDus con progreso y une automáticamente las partes de 250 MB.", 15, false);
        intro.setTextColor(Color.rgb(180, 205, 220));
        root.addView(intro, margin(0, 12, 0, 18));

        codeInput = new EditText(this);
        codeInput.setHint("Código enviado por /app");
        codeInput.setSingleLine(true);
        codeInput.setTextColor(Color.WHITE);
        codeInput.setHintTextColor(Color.rgb(130, 155, 170));
        root.addView(codeInput, margin(0, 4, 0, 8));
        Button pair = new Button(this);
        pair.setText("Vincular con Telegram");
        pair.setOnClickListener(v -> pairDevice());
        root.addView(pair);

        status = text(token.isEmpty() ? "Abre Telegram y envía /app al bot Tod4S3." : "Aplicación vinculada.", 14, false);
        status.setTextColor(Color.rgb(99, 230, 216));
        root.addView(status, margin(0, 14, 0, 18));

        TextView heading = text("Entregas recientes", 20, true);
        root.addView(heading);
        deliveriesBox = new LinearLayout(this);
        deliveriesBox.setOrientation(LinearLayout.VERTICAL);
        root.addView(deliveriesBox, margin(0, 8, 0, 0));
        setContentView(scroll);
    }

    private TextView text(String value, int size, boolean bold) {
        TextView v = new TextView(this);
        v.setText(value);
        v.setTextColor(Color.WHITE);
        v.setTextSize(size);
        if (bold) v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return v;
    }

    private LinearLayout.LayoutParams margin(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.setMargins(l, t, r, b);
        return p;
    }

    private String deviceId() {
        String id = prefs.getString("device_id", "");
        if (id.isEmpty()) {
            id = Settings.Secure.getString(getContentResolver(), Settings.Secure.ANDROID_ID);
            if (id == null || id.isEmpty()) id = UUID.randomUUID().toString();
            prefs.edit().putString("device_id", id).apply();
        }
        return id;
    }

    private void pairDevice() {
        final String code = codeInput.getText().toString().trim();
        if (code.isEmpty()) { status.setText("Escribe el código de Telegram."); return; }
        status.setText("Vinculando…");
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject().put("code", code).put("device_id", deviceId());
                JSONObject result = request("POST", "/api/app/pair", body.toString(), "");
                String newToken = result.optString("token", "");
                if (newToken.isEmpty()) throw new Exception("Código inválido o vencido");
                prefs.edit().putString("token", newToken).apply();
                token = newToken;
                runOnUiThread(() -> { status.setText("Vinculación completada."); refreshDeliveries(); });
            } catch (Exception e) {
                runOnUiThread(() -> status.setText("No se pudo vincular: " + e.getMessage()));
            }
        }).start();
    }

    private void refreshDeliveries() {
        final String current = token;
        if (current == null || current.isEmpty()) return;
        new Thread(() -> {
            try {
                JSONObject result = request("GET", "/api/app/deliveries", "", current);
                JSONArray rows = result.optJSONArray("deliveries");
                runOnUiThread(() -> renderDeliveries(rows));
            } catch (Exception e) {
                runOnUiThread(() -> status.setText("Servidor no disponible; se reintentará."));
            }
        }).start();
    }

    private void renderDeliveries(JSONArray rows) {
        deliveriesBox.removeAllViews();
        if (rows == null || rows.length() == 0) {
            deliveriesBox.addView(text("Todavía no hay archivos nuevos.", 14, false));
            return;
        }
        for (int i = 0; i < rows.length(); i++) {
            try {
                JSONObject row = rows.getJSONObject(i);
                String name = row.optString("filename", "archivo");
                long size = row.optLong("size", 0);
                JSONArray parts = row.optJSONArray("parts");
                LinearLayout card = new LinearLayout(this);
                card.setOrientation(LinearLayout.VERTICAL);
                card.setPadding(20, 18, 20, 18);
                card.setBackgroundColor(Color.rgb(18, 39, 56));
                TextView label = text(name + "\n" + formatBytes(size) + " · " + (parts == null ? 0 : parts.length()) + " parte(s)", 15, true);
                card.addView(label);
                Button download = new Button(this);
                download.setText("Descargar y unir");
                final JSONArray copy = parts;
                final String copyName = name;
                download.setOnClickListener(v -> downloadParts(copyName, copy));
                card.addView(download);
                deliveriesBox.addView(card, margin(0, 0, 0, 12));
            } catch (Exception ignored) { }
        }
    }

    private String formatBytes(long n) {
        if (n < 1024 * 1024) return (n / 1024) + " KB";
        return String.format(java.util.Locale.US, "%.2f MB", n / 1048576.0);
    }

    private void downloadParts(String name, JSONArray parts) {
        if (parts == null || parts.length() == 0) return;
        status.setText("Descargando " + name + "…");
        new Thread(() -> {
            File tempDir = new File(getCacheDir(), "parts_" + System.currentTimeMillis());
            if (!tempDir.mkdirs()) return;
            try {
                File output = new File(getExternalFilesDir("Download"), safeName(name));
                try (FileOutputStream out = new FileOutputStream(output)) {
                    byte[] buffer = new byte[65536];
                    for (int i = 0; i < parts.length(); i++) {
                        File part = new File(tempDir, "part" + i);
                        downloadUrl(parts.getString(i), part);
                        try (FileInputStream in = new FileInputStream(part)) {
                            int count;
                            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
                        }
                        part.delete();
                    }
                }
                deleteTree(tempDir);
                runOnUiThread(() -> status.setText("Listo: " + output.getAbsolutePath()));
            } catch (Exception e) {
                deleteTree(tempDir);
                runOnUiThread(() -> status.setText("Error de descarga: " + e.getMessage()));
            }
        }).start();
    }

    private void downloadUrl(String raw, File dest) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(raw).openConnection();
        c.setConnectTimeout(20_000); c.setReadTimeout(60_000); c.setRequestProperty("Accept-Encoding", "identity");
        if (c.getResponseCode() / 100 != 2) throw new Exception("HTTP " + c.getResponseCode());
        try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(dest)) {
            byte[] buffer = new byte[65536]; int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
        } finally { c.disconnect(); }
    }

    private String safeName(String name) {
        String clean = name.replaceAll("[^a-zA-Z0-9._-]", "_");
        return clean.isEmpty() ? "archivo.bin" : clean;
    }

    private void deleteTree(File f) {
        if (f.isDirectory()) { File[] children = f.listFiles(); if (children != null) for (File c : children) deleteTree(c); }
        f.delete();
    }

    private JSONObject request(String method, String path, String body, String bearer) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(API_BASE + path).openConnection();
        c.setRequestMethod(method); c.setConnectTimeout(15_000); c.setReadTimeout(30_000);
        c.setRequestProperty("Accept", "application/json");
        if (!bearer.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + bearer);
        if (!body.isEmpty()) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream out = c.getOutputStream()) { out.write(body.getBytes(StandardCharsets.UTF_8)); }
        }
        InputStream stream = c.getResponseCode() >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int count;
        while ((count = stream.read(chunk)) != -1) buffer.write(chunk, 0, count);
        byte[] bytes = buffer.toByteArray();
        int code = c.getResponseCode(); c.disconnect();
        if (code / 100 != 2) throw new Exception("HTTP " + code);
        return new JSONObject(new String(bytes, StandardCharsets.UTF_8));
    }

    @Override protected void onDestroy() {
        handler.removeCallbacks(poller);
        super.onDestroy();
    }
}
