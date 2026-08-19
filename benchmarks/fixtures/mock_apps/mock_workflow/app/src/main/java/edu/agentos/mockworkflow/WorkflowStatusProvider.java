package edu.agentos.mockworkflow;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;

public class WorkflowStatusProvider extends ContentProvider {
    static final String PREFS = "workflow_status";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        SharedPreferences prefs = prefs();
        MatrixCursor cursor = new MatrixCursor(new String[] {"package", "scenario", "status", "value"});
        cursor.addRow(new Object[] {
                getContext().getPackageName(),
                prefs.getString("scenario", ""),
                prefs.getString("status", "not_started"),
                prefs.getString("value", "")
        });
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        if (values != null) {
            SharedPreferences.Editor editor = prefs().edit();
            if (values.containsKey("scenario")) {
                editor.putString("scenario", values.getAsString("scenario"));
            }
            if (values.containsKey("status")) {
                editor.putString("status", values.getAsString("status"));
            }
            if (values.containsKey("value")) {
                editor.putString("value", values.getAsString("value"));
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
        return "vnd.android.cursor.item/mock-workflow-status";
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
