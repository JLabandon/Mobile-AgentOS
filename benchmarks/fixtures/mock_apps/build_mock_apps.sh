#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BT="$SDK/build-tools/36.0.0"
ANDROID_JAR="$SDK/platforms/android-36/android.jar"
OUT="$ROOT/build"

build_app() {
  local name="$1"
  local package="$2"
  local src="$ROOT/$name"
  local build="$OUT/$name"
  rm -rf "$build"
  mkdir -p "$build/compiled" "$build/classes" "$build/dex"
  "$BT/aapt2" compile --dir "$src/res" -o "$build/compiled/resources.zip"
  "$BT/aapt2" link -o "$build/base.apk" -I "$ANDROID_JAR" --manifest "$src/AndroidManifest.xml" "$build/compiled/resources.zip" --java "$build/gen"
  find "$src/app/src/main/java" -name '*.java' | sed 's/.*/"&"/' > "$build/sources.txt"
  javac -source 11 -target 11 -classpath "$ANDROID_JAR" -d "$build/classes" @"$build/sources.txt"
  cd "$build/classes"
  "$BT/d8" --lib "$ANDROID_JAR" --output "$build/dex" $(find . -name '*.class')
  cd "$ROOT"
  cp "$build/base.apk" "$build/unsigned.apk"
  cd "$build/dex"
  zip -q "$build/unsigned.apk" classes.dex
  cd "$ROOT"
  "$BT/zipalign" -f 4 "$build/unsigned.apk" "$build/aligned.apk"
  "$BT/apksigner" sign --ks "$HOME/.android/debug.keystore" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out "$OUT/$package.apk" "$build/aligned.apk"
}

build_workflow_app() {
  local package="$1"
  local label="$2"
  local scenario="$3"
  local src="$ROOT/mock_workflow"
  local temp="$OUT/mock_workflow_$scenario/src"
  rm -rf "$OUT/mock_workflow_$scenario"
  mkdir -p "$temp"
  cp -R "$src/." "$temp/"
  perl -0pi -e "s/package=\"edu.agentos.mockworkflow\"/package=\"$package\"/" "$temp/AndroidManifest.xml"
  perl -0pi -e "s/__STATUS_AUTHORITY__/$package.status/" "$temp/AndroidManifest.xml"
  perl -0pi -e 's/android:name="\.MainActivity"/android:name="edu.agentos.mockworkflow.MainActivity"/g' "$temp/AndroidManifest.xml"
  cat > "$temp/res/values/strings.xml" <<EOF
<resources>
    <string name="app_name">$label</string>
    <string name="scenario">$scenario</string>
</resources>
EOF
  build_app "build/mock_workflow_$scenario/src" "$package"
}

build_app mock_shop edu.agentos.mockshop
build_app mock_payment edu.agentos.mockpayment
build_workflow_app edu.agentos.mockplanner "Mock Planner" planner
build_workflow_app edu.agentos.mocktaska "Mock Task A" task_a
build_workflow_app edu.agentos.mocktaskb "Mock Task B" task_b
build_workflow_app edu.agentos.mockplannerlocal "Mock Planner Local" planner_local
build_workflow_app edu.agentos.mocktaskalocal "Mock Task A Local" task_a_local
build_workflow_app edu.agentos.mocktaskblocal "Mock Task B Local" task_b_local

echo "$OUT/edu.agentos.mockshop.apk"
echo "$OUT/edu.agentos.mockpayment.apk"
echo "$OUT/edu.agentos.mockplanner.apk"
echo "$OUT/edu.agentos.mocktaska.apk"
echo "$OUT/edu.agentos.mocktaskb.apk"
echo "$OUT/edu.agentos.mockplannerlocal.apk"
echo "$OUT/edu.agentos.mocktaskalocal.apk"
echo "$OUT/edu.agentos.mocktaskblocal.apk"
