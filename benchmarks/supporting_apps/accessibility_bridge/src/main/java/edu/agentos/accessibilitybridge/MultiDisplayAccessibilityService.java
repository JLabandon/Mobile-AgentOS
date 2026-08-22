package edu.agentos.accessibilitybridge;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.os.Build;
import android.util.Base64;
import android.util.SparseArray;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.List;

public final class MultiDisplayAccessibilityService extends AccessibilityService {
    private static volatile MultiDisplayAccessibilityService instance;

    @Override
    protected void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onDestroy() {
        if (instance == this) {
            instance = null;
        }
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Snapshots are queried synchronously through the provider.
    }

    @Override
    public void onInterrupt() {
    }

    static String snapshotBase64(int displayId, String expectedPackage, int limit) {
        MultiDisplayAccessibilityService service = instance;
        if (service == null) {
            return encode(error("service_unavailable"));
        }
        return encode(service.snapshot(displayId, expectedPackage, Math.max(1, Math.min(limit, 300))));
    }

    private synchronized JSONObject snapshot(int displayId, String expectedPackage, int limit) {
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                return error("getWindowsOnAllDisplays_requires_api_30");
            }
            SparseArray<List<AccessibilityWindowInfo>> byDisplay = getWindowsOnAllDisplays();
            List<AccessibilityWindowInfo> windows = byDisplay.get(displayId);
            if (windows == null || windows.isEmpty()) {
                return error("no_interactive_windows_on_display");
            }
            for (AccessibilityWindowInfo window : windows) {
                AccessibilityNodeInfo root = window.getRoot();
                if (root == null) {
                    continue;
                }
                try {
                    String packageName = asString(root.getPackageName());
                    if (!expectedPackage.equals(packageName)) {
                        continue;
                    }
                    JSONArray nodes = new JSONArray();
                    appendNodes(root, nodes, limit);
                    JSONObject result = new JSONObject();
                    result.put("ok", true);
                    result.put("display_id", displayId);
                    result.put("package", packageName);
                    result.put("window_id", window.getId());
                    result.put("nodes", nodes);
                    return result;
                } finally {
                    root.recycle();
                }
            }
            return error("expected_package_not_found");
        } catch (Exception exception) {
            return error(exception.getClass().getSimpleName() + ": " + exception.getMessage());
        }
    }

    private static void appendNodes(AccessibilityNodeInfo node, JSONArray out, int limit) throws JSONException {
        if (out.length() >= limit) {
            return;
        }
        String text = asString(node.getText());
        String description = asString(node.getContentDescription());
        boolean clickable = node.isClickable();
        boolean editable = node.isEditable();
        boolean focusable = node.isFocusable();
        if (!text.isEmpty() || !description.isEmpty() || clickable || editable || focusable) {
            Rect bounds = new Rect();
            node.getBoundsInScreen(bounds);
            JSONObject value = new JSONObject();
            value.put("text", text);
            value.put("content_desc", description);
            value.put("resource_id", asString(node.getViewIdResourceName()));
            value.put("class_name", asString(node.getClassName()));
            value.put("package", asString(node.getPackageName()));
            value.put("bounds", new JSONArray(new int[]{bounds.left, bounds.top, bounds.right, bounds.bottom}));
            value.put("clickable", clickable);
            value.put("enabled", node.isEnabled());
            value.put("editable", editable);
            value.put("checkable", node.isCheckable());
            value.put("checked", node.isChecked());
            value.put("selected", node.isSelected());
            value.put("focused", node.isFocused());
            out.put(value);
        }
        for (int index = 0; index < node.getChildCount() && out.length() < limit; index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) {
                continue;
            }
            try {
                appendNodes(child, out, limit);
            } finally {
                child.recycle();
            }
        }
    }

    private static String asString(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static JSONObject error(String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("ok", false);
            result.put("error", message == null ? "unknown_error" : message);
        } catch (JSONException ignored) {
        }
        return result;
    }

    private static String encode(JSONObject value) {
        return Base64.encodeToString(value.toString().getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }
}
