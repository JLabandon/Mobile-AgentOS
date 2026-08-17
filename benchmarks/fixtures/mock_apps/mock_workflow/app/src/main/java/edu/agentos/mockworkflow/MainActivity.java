package edu.agentos.mockworkflow;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    private String scenario;
    private TextView status;
    private TextView detail;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        scenario = getString(getResources().getIdentifier("scenario", "string", getPackageName()));
        render(false);
    }

    private void render(boolean completed) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(24), dp(24), dp(24), dp(24));
        root.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView title = new TextView(this);
        title.setTextSize(28);
        title.setGravity(Gravity.START);
        title.setText(titleText());
        root.addView(title);

        TextView instruction = new TextView(this);
        instruction.setTextSize(18);
        instruction.setPadding(0, dp(18), 0, dp(18));
        instruction.setText(instructionText());
        root.addView(instruction);

        detail = new TextView(this);
        detail.setTextSize(20);
        detail.setPadding(0, dp(14), 0, dp(14));
        detail.setText(completed ? completedDetailText() : missingDetailText());
        root.addView(detail);

        status = new TextView(this);
        status.setTextSize(22);
        status.setPadding(0, dp(18), 0, dp(18));
        status.setText(completed ? successText() : "Status: waiting for peer information");
        root.addView(status);

        Button complete = new Button(this);
        complete.setAllCaps(false);
        complete.setText(buttonText());
        complete.setContentDescription(buttonText());
        complete.setOnClickListener(v -> render(true));
        root.addView(complete, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(64)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    private String titleText() {
        if ("task_a".equals(scenario)) return "Task A Workspace";
        if ("task_c".equals(scenario)) return "Task C Workspace";
        return "Planner Workspace";
    }

    private String instructionText() {
        if ("task_a".equals(scenario)) return "Complete Task A only after receiving Project Alpha code from Google Keep.";
        if ("task_c".equals(scenario)) return "Complete Task C only after receiving Project Beta code from Google Keep.";
        return "Complete the appointment only after receiving the appointment location from Google Keep.";
    }

    private String missingDetailText() {
        if ("task_a".equals(scenario)) return "Required field: Project Alpha code is missing.";
        if ("task_c".equals(scenario)) return "Required field: Project Beta code is missing.";
        return "Required field: appointment location is missing.";
    }

    private String completedDetailText() {
        if ("task_a".equals(scenario)) return "Project Alpha code accepted from peer response.";
        if ("task_c".equals(scenario)) return "Project Beta code accepted from peer response.";
        return "Appointment location accepted from peer response.";
    }

    private String successText() {
        if ("task_a".equals(scenario)) return "Status: Task A complete";
        if ("task_c".equals(scenario)) return "Status: Task C complete";
        return "Status: Appointment complete";
    }

    private String buttonText() {
        if ("task_a".equals(scenario)) return "Complete Task A with peer response";
        if ("task_c".equals(scenario)) return "Complete Task C with peer response";
        return "Complete appointment with peer response";
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
