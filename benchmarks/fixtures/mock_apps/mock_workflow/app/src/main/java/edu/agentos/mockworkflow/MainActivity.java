package edu.agentos.mockworkflow;

import android.app.Activity;
import android.content.Context;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    private String scenario;
    private TextView status;
    private TextView detail;
    private EditText input;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        scenario = getString(getResources().getIdentifier("scenario", "string", getPackageName()));
        render(false, "");
    }

    private void render(boolean completed, String previousValue) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(24), dp(24), dp(24), dp(24));
        root.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView title = new TextView(this);
        title.setTextSize(28);
        title.setGravity(Gravity.START);
        title.setText(titleText());
        root.addView(title);

        detail = new TextView(this);
        detail.setTextSize(20);
        detail.setPadding(0, dp(14), 0, dp(14));
        detail.setText(completed ? completedDetailText() : missingDetailText());
        root.addView(detail);

        input = new EditText(this);
        input.setSingleLine(true);
        input.setText(previousValue);
        input.setHint(inputHint());
        input.setContentDescription(inputHint());
        root.addView(input, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(64)));

        status = new TextView(this);
        status.setTextSize(22);
        status.setPadding(0, dp(18), 0, dp(18));
        String visibleStatus = completed ? successText() : "Status: required field empty";
        status.setText(visibleStatus);
        storeStatus(visibleStatus, previousValue);
        root.addView(status);

        Button complete = new Button(this);
        complete.setAllCaps(false);
        complete.setText(buttonText());
        complete.setContentDescription(buttonText());
        complete.setOnClickListener(v -> {
            String value = input.getText().toString().trim();
            if (matchesExpected(value)) {
                render(true, value);
            } else if (value.isEmpty()) {
                status.setText("Status: required field empty");
            } else {
                status.setText("Status: value not accepted");
            }
        });
        root.addView(complete, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(64)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    private String titleText() {
        if ("task_a".equals(scenario)) return "Procurement Portal";
        if ("task_b".equals(scenario)) return "Shipping Portal";
        if ("planner_local".equals(scenario)) return "Clinic Check-In";
        if ("task_a_local".equals(scenario)) return "Inventory Request";
        if ("task_b_local".equals(scenario)) return "Compliance Filing";
        return "Service Case Desk";
    }

    private String missingDetailText() {
        if ("planner_local".equals(scenario)) return "Patient: Alice\nVisit time: tomorrow 3:00 PM\nReference location: Googleplex\nRequired field: clinic location";
        if ("task_a_local".equals(scenario)) return "Item: Lab sensor pack\nInventory reference: ALPHA-42\nRequired field: inventory reference";
        if ("task_b_local".equals(scenario)) return "Filing: Export compliance memo\nCompliance reference: BETA-73\nRequired field: compliance reference";
        if ("task_a".equals(scenario)) return "Purchase request: Project Alpha equipment\nRequired field: procurement project code";
        if ("task_b".equals(scenario)) return "Shipment request: Project Beta prototype kit\nRequired field: shipping project code";
        return "Case: R-482\nRequired field: verification token";
    }

    private String completedDetailText() {
        if ("task_a".equals(scenario)) return "Procurement request: Project Alpha\nProject code accepted";
        if ("task_b".equals(scenario)) return "Shipping request: Project Beta\nProject code accepted";
        if ("planner_local".equals(scenario)) return "Patient: Alice\nClinic location accepted";
        if ("task_a_local".equals(scenario)) return "Inventory request\nStock code accepted";
        if ("task_b_local".equals(scenario)) return "Compliance filing\nFiling reference accepted";
        return "Case R-482\nVerification token accepted";
    }

    private String successText() {
        if ("task_a".equals(scenario)) return "Status: Procurement request complete";
        if ("task_b".equals(scenario)) return "Status: Shipping request complete";
        if ("planner_local".equals(scenario)) return "Status: Appointment complete";
        if ("task_a_local".equals(scenario)) return "Status: Inventory request complete";
        if ("task_b_local".equals(scenario)) return "Status: Compliance filing complete";
        return "Status: Service case submitted";
    }

    private String buttonText() {
        if ("task_a".equals(scenario)) return "Submit Procurement Request";
        if ("task_b".equals(scenario)) return "Submit Shipping Request";
        if ("planner_local".equals(scenario)) return "Complete Clinic Check-In";
        if ("task_a_local".equals(scenario)) return "Submit Inventory Request";
        if ("task_b_local".equals(scenario)) return "Submit Compliance Filing";
        return "Submit Service Case";
    }

    private String inputHint() {
        if ("task_a".equals(scenario)) return "Enter procurement project code";
        if ("task_b".equals(scenario)) return "Enter shipping project code";
        if ("task_a_local".equals(scenario)) return "Enter inventory reference";
        if ("task_b_local".equals(scenario)) return "Enter compliance reference";
        return "Enter verification token";
    }

    private boolean matchesExpected(String value) {
        String normalized = value.trim().toLowerCase();
        if ("planner_local".equals(scenario)) return normalized.equals("googleplex");
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return normalized.equals("alpha-42");
        if ("task_b".equals(scenario) || "task_b_local".equals(scenario)) return normalized.equals("beta-73");
        return normalized.equals("h9k-27");
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void storeStatus(String visibleStatus, String value) {
        getSharedPreferences(WorkflowStatusProvider.PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString("scenario", scenario)
                .putString("status", visibleStatus)
                .putString("value", value == null ? "" : value)
                .commit();
    }
}
