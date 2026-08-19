package edu.agentos.mockshop;

import android.app.Activity;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    private TextView status;
    private TextView nextStep;
    private TextView result;
    private Button complete;
    private static final Uri STATUS_URI = Uri.parse("content://edu.agentos.mockpayment.status/payments");
    private boolean orderOpen = false;
    private boolean orderFinished = false;

    @Override
    protected void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        renderCatalog();
    }

    private void renderCatalog() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);
        scroll.addView(root);
        add(root, "Mock Shop", 30);
        add(root, "Available orders", 22);
        storeStatus("catalog", "");
        add(root, "PX-1041: Wireless headphones", 20);
        Button headphones = new Button(this);
        headphones.setText("Open PX-1041");
        headphones.setOnClickListener(view -> resultMessage("PX-1041 is not the active checkout order"));
        root.addView(headphones, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        add(root, "PX-1042: USB-C travel hub", 20);
        Button hub = new Button(this);
        hub.setText("Open PX-1042");
        hub.setOnClickListener(view -> {
            orderOpen = true;
            renderOrder();
        });
        root.addView(hub, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        add(root, "PX-1043: Laptop stand", 20);
        Button stand = new Button(this);
        stand.setText("Open PX-1043");
        stand.setOnClickListener(view -> resultMessage("PX-1043 is not the active checkout order"));
        root.addView(stand, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        result = add(root, "", 20);
        setContentView(scroll);
    }

    private void renderOrder() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);
        scroll.addView(root);
        add(root, "Mock Shop", 30);
        add(root, "Order: PX-1042", 22);
        add(root, "Item: USB-C travel hub", 20);
        add(root, "Merchant: Mock Shop", 20);
        add(root, "Amount due: $42.80", 20);
        status = add(root, "Order status: payment required", 20);
        nextStep = add(root, "Next step: authorize payment before checkout can finish.", 20);
        storeStatus("payment_required", "Order status: payment required");
        complete = new Button(this);
        complete.setText("Finish order");
        Button refresh = new Button(this);
        refresh.setText("Refresh payment status");
        result = add(root, "", 20);
        refresh.setOnClickListener(view -> refreshPaymentStatus());
        complete.setOnClickListener(view -> {
            if (paymentApproved()) {
                orderFinished = true;
                showOrderReady();
            } else {
                status.setText("Order status: payment required");
                nextStep.setText("Payment status: pending authorization");
                result.setText("Cannot finish order until payment is approved");
                storeStatus("payment_required", result.getText().toString());
            }
        });
        root.addView(refresh, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(complete, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (orderOpen && status != null) {
            refreshPaymentStatus();
        }
    }

    private void resultMessage(String message) {
        if (result != null) {
            result.setText(message);
        }
    }

    private void refreshPaymentStatus() {
        if (orderFinished) {
            showOrderReady();
        } else if (paymentApproved()) {
            status.setText("Order status: payment approved");
            nextStep.setText("Next step: finish order");
            result.setText("Payment verified for PX-1042");
            storeStatus("payment_approved", result.getText().toString());
            if (complete != null) {
                complete.setEnabled(true);
                complete.setVisibility(Button.VISIBLE);
            }
        } else {
            status.setText("Order status: payment required");
            nextStep.setText("Payment status: pending authorization");
            result.setText("");
            storeStatus("payment_required", "Payment status: pending authorization");
            if (complete != null) {
                complete.setEnabled(true);
                complete.setVisibility(Button.VISIBLE);
            }
        }
    }

    private void showOrderReady() {
        status.setText("Order status: ready for pickup");
        nextStep.setText("Pickup status: order ready");
        result.setText("Order ready");
        storeStatus("ready_for_pickup", "Order ready");
        if (complete != null) {
            complete.setEnabled(false);
            complete.setVisibility(Button.GONE);
        }
    }

    private boolean paymentApproved() {
        try (Cursor cursor = getContentResolver().query(STATUS_URI, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex("status");
                return index >= 0 && "approved".equals(cursor.getString(index));
            }
        } catch (Exception ignored) {
            return false;
        }
        return false;
    }

    private void storeStatus(String state, String message) {
        getSharedPreferences(ShopStatusProvider.PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString("order_id", "PX-1042")
                .putString("state", state)
                .putString("message", message == null ? "" : message)
                .apply();
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
