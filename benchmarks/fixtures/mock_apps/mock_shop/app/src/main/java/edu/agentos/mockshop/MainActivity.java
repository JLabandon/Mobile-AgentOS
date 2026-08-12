package edu.agentos.mockshop;

import android.app.Activity;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    private TextView status;

    @Override
    protected void onCreate(Bundle bundle) {
        super.onCreate(bundle);
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
        status = add(root, "Status: awaiting payment authorization", 20);
        add(root, "After payment: use the completion control to update this order.", 20);
        Button complete = new Button(this);
        complete.setText("COMPLETE AFTER PAYMENT");
        complete.setOnClickListener(view -> status.setText("Status: ready for pickup"));
        root.addView(complete, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        add(root, "Corner case note: This app cannot finish until a peer payment agent returns an operation result.", 18);
        setContentView(scroll);
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
