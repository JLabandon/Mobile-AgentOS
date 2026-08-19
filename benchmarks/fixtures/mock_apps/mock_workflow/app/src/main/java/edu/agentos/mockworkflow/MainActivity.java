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
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return "Task A Workspace";
        if ("task_c".equals(scenario) || "task_c_local".equals(scenario)) return "Task C Workspace";
        return "Planner Workspace";
    }

    private String missingDetailText() {
        if ("planner_local".equals(scenario)) return "Appointment: Alice, tomorrow 3:00 PM\nReference location: Googleplex\nRequired field: location";
        if ("task_a_local".equals(scenario)) return "Project: Alpha\nReference code: ALPHA-42\nRequired field: project code";
        if ("task_c_local".equals(scenario)) return "Project: Beta\nReference code: BETA-73\nRequired field: project code";
        if ("task_a".equals(scenario)) return "Project: Alpha\nRequired field: project code";
        if ("task_c".equals(scenario)) return "Project: Beta\nRequired field: project code";
        return "Appointment: Alice, tomorrow 3:00 PM\nRequired field: location";
    }

    private String completedDetailText() {
        if ("task_a".equals(scenario)) return "Project: Alpha\nProject code accepted";
        if ("task_c".equals(scenario)) return "Project: Beta\nProject code accepted";
        return "Appointment: Alice, tomorrow 3:00 PM\nLocation accepted";
    }

    private String successText() {
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return "Status: Task A complete";
        if ("task_c".equals(scenario) || "task_c_local".equals(scenario)) return "Status: Task C complete";
        return "Status: Appointment complete";
    }

    private String buttonText() {
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return "Complete Task A";
        if ("task_c".equals(scenario) || "task_c_local".equals(scenario)) return "Complete Task C";
        return "Complete Appointment";
    }

    private String inputHint() {
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return "Enter project code";
        if ("task_c".equals(scenario) || "task_c_local".equals(scenario)) return "Enter project code";
        return "Enter appointment location";
    }

    private boolean matchesExpected(String value) {
        String normalized = value.trim().toLowerCase();
        if ("task_a".equals(scenario) || "task_a_local".equals(scenario)) return normalized.equals("alpha-42");
        if ("task_c".equals(scenario) || "task_c_local".equals(scenario)) return normalized.equals("beta-73");
        return normalized.equals("googleplex");
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
