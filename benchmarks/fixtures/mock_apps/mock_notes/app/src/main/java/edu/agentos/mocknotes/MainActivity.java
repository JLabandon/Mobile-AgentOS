package edu.agentos.mocknotes;

import android.app.Activity;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle bundle) {
        super.onCreate(bundle);

        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);
        scroll.addView(root);

        addText(root, "Notes", 30);

        EditText search = new EditText(this);
        search.setSingleLine(true);
        search.setHint("Search notes");
        search.setContentDescription("Search notes");
        root.addView(search, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        addNote(root, "Research Sync", "Location: Googleplex\nNotes: discuss Mobile AgentOS runtime handoff");
        addNote(root, "Service case R-482", "Verification token: H9K-27\nCustomer requested an on-site visit");
        addNote(root, "Project Alpha procurement", "Project code: ALPHA-42\nApproval code: AP-884\nOwner: Maya");
        addNote(root, "Morning Flight", "Wake-up time: 4:00 PM\nRecommended leave time: 4:30 PM\nFlight: UA 238");
        addNote(root, "Project Beta shipping", "Project code: BETA-73\nRouting code: RT-219\nDestination: Building C loading dock");
        addNote(root, "Lunch Ideas", "Try the noodle shop near Central Ave\nBackup option: salad place downstairs");
        addNote(root, "Desk Setup", "USB-C hub, monitor cable, spare keyboard");

        setContentView(scroll);
    }

    private void addNote(LinearLayout root, String title, String body) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(24, 22, 24, 22);
        card.setBackgroundColor(0xFFFFFFFF);
        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(22);
        titleView.setPadding(0, 0, 0, 8);
        TextView bodyView = new TextView(this);
        bodyView.setText(body);
        bodyView.setTextSize(18);
        bodyView.setPadding(0, 0, 0, 0);
        card.addView(titleView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        card.addView(bodyView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 16, 0, 0);
        root.addView(card, params);
    }

    private void addText(LinearLayout root, String text, int sp) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sp);
        view.setPadding(0, 10, 0, 10);
        root.addView(view, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
    }
}
