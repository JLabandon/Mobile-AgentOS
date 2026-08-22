# Multi-Display Accessibility Bridge

This benchmark-support component exposes an Android `AccessibilityService` snapshot through a `ContentProvider`. Every request identifies one logical `display_id` and one expected package. The response contains nodes only from the matching window.

It provides the display-scoped structured observation surface used by `AndroidUiDriver`. It contains no task, app, or agent-specific behavior.

## Deploy

```bash
./build.sh
adb install -r agentos-accessibility-bridge.apk
adb shell settings put secure enabled_accessibility_services \
  "$(adb shell settings get secure enabled_accessibility_services):edu.agentos.accessibilitybridge/edu.agentos.accessibilitybridge.MultiDisplayAccessibilityService"
adb shell settings put secure accessibility_enabled 1
```

## Query

```bash
adb shell content call \
  --uri content://edu.agentos.accessibilitybridge.snapshot \
  --method snapshot \
  --extra display_id:i:2 \
  --extra package:s:edu.agentos.mockplannerlocal
```

The `payload_b64` response decodes to a JSON object with `display_id`, `package`, `window_id`, and a list of visible nodes.
