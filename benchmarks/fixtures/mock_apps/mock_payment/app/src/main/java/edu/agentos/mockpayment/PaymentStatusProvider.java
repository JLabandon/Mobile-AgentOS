package edu.agentos.mockpayment;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;

public class PaymentStatusProvider extends ContentProvider {
    public static final String AUTHORITY = "edu.agentos.mockpayment.status";
    public static final String ORDER_ID = "PX-1042";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        MatrixCursor cursor = new MatrixCursor(new String[] {"order_id", "status"});
        cursor.addRow(new Object[] {ORDER_ID, prefs().getString(ORDER_ID, "pending")});
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        if (values != null && ORDER_ID.equals(values.getAsString("order_id"))) {
            prefs().edit().putString(ORDER_ID, values.getAsString("status")).apply();
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
        prefs().edit().remove(ORDER_ID).apply();
        return 1;
    }

    @Override
    public String getType(Uri uri) {
        return "vnd.android.cursor.item/mock-payment-status";
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences("payment_status", Context.MODE_PRIVATE);
    }
}
