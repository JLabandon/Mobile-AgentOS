package edu.agentos.mockshop;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;

public class ShopStatusProvider extends ContentProvider {
    static final String PREFS = "shop_status";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        SharedPreferences prefs = prefs();
        MatrixCursor cursor = new MatrixCursor(new String[] {"order_id", "state", "message"});
        cursor.addRow(new Object[] {
                prefs.getString("order_id", ""),
                prefs.getString("state", "not_started"),
                prefs.getString("message", "")
        });
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        if (values != null) {
            SharedPreferences.Editor editor = prefs().edit();
            if (values.containsKey("order_id")) {
                editor.putString("order_id", values.getAsString("order_id"));
            }
            if (values.containsKey("state")) {
                editor.putString("state", values.getAsString("state"));
            }
            if (values.containsKey("message")) {
                editor.putString("message", values.getAsString("message"));
            }
            editor.apply();
        }
        return uri;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        insert(uri, values);
        return 1;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        prefs().edit().clear().apply();
        return 1;
    }

    @Override
    public String getType(Uri uri) {
        return "vnd.android.cursor.item/mock-shop-status";
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
