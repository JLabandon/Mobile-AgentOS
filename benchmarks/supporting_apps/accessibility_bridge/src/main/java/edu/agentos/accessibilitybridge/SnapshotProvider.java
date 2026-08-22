package edu.agentos.accessibilitybridge;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;

public final class SnapshotProvider extends ContentProvider {
    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        Bundle result = new Bundle();
        if (!"snapshot".equals(method)) {
            result.putString("error", "unsupported_method");
            return result;
        }
        int displayId = extras == null ? -1 : extras.getInt("display_id", -1);
        String expectedPackage = extras == null ? "" : extras.getString("package", "");
        int limit = extras == null ? 160 : extras.getInt("limit", 160);
        if (displayId < 0 || expectedPackage.isEmpty()) {
            result.putString("error", "display_id_and_package_required");
            return result;
        }
        result.putString("payload_b64", MultiDisplayAccessibilityService.snapshotBase64(displayId, expectedPackage, limit));
        return result;
    }

    @Override public String getType(Uri uri) { return "application/json"; }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { return 0; }
}
