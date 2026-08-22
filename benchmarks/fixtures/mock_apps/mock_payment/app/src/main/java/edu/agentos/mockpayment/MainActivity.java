package edu.agentos.mockpayment;

import android.app.Activity;
import android.content.ContentValues;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    private TextView status;
    private static final String ORDER_ID = "PX-1042";
    private static final Uri STATUS_URI = Uri.parse("content://edu.agentos.mockpayment.status/payments");

    @Override
    protected void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);
        scroll.addView(root);
        add(root, "Campus Wallet", 30);
        add(root, "Payment request: PX-1042", 22);
        add(root, "Merchant: Campus Store", 20);
        add(root, "Amount: $42.80", 20);
        status = add(root, "Payment status: pending authorization", 20);
        Button approve = new Button(this);
        Button decline = new Button(this);
        approve.setText("Approve Payment");
        decline.setText("Decline Payment");
        approve.setOnClickListener(view -> {
            writePaymentStatus("approved");
            status.setText("Payment status: approved for PX-1042");
            approve.setEnabled(false);
            decline.setEnabled(false);
            approve.setVisibility(Button.GONE);
            decline.setVisibility(Button.GONE);
        });
        decline.setOnClickListener(view -> {
            writePaymentStatus("declined");
            status.setText("Payment status: declined for PX-1042");
        });
        root.addView(approve, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(decline, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
    }

    private void writePaymentStatus(String value) {
        ContentValues values = new ContentValues();
        values.put("order_id", ORDER_ID);
        values.put("status", value);
        getContentResolver().insert(STATUS_URI, values);
    }

    private TextView add(LinearLayout root, String text, int sp) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sp);
        view.setPadding(0, 12, 0, 12);
        root.addView(view, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return view;
    }
}
