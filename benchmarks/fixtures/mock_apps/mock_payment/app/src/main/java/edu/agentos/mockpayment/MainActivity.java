package edu.agentos.mockpayment;

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
        add(root, "Mock Payment", 30);
        add(root, "Payment request: PX-1042", 22);
        add(root, "Merchant: Mock Shop", 20);
        add(root, "Amount: $42.80", 20);
        status = add(root, "Payment status: pending authorization", 20);
        Button approve = new Button(this);
        approve.setText("Approve Payment");
        approve.setOnClickListener(view -> status.setText("Payment status: approved for PX-1042"));
        root.addView(approve, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button decline = new Button(this);
        decline.setText("Decline Payment");
        decline.setOnClickListener(view -> status.setText("Payment status: declined for PX-1042"));
        root.addView(decline, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        add(root, "Corner case note: Operation response must include success or failure and visible evidence.", 18);
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
