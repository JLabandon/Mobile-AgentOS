#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BT="$SDK/build-tools/36.0.0"
ANDROID_JAR="$SDK/platforms/android-36/android.jar"
BUILD="$ROOT/build"
OUT="$ROOT/agentos-accessibility-bridge.apk"

rm -rf "$BUILD"
mkdir -p "$BUILD/compiled" "$BUILD/classes" "$BUILD/dex"
"$BT/aapt2" compile --dir "$ROOT/res" -o "$BUILD/compiled/resources.zip"
"$BT/aapt2" link -o "$BUILD/base.apk" -I "$ANDROID_JAR" --manifest "$ROOT/AndroidManifest.xml" "$BUILD/compiled/resources.zip" --java "$BUILD/gen"
find "$ROOT/src/main/java" -name '*.java' | sed 's/.*/"&"/' > "$BUILD/sources.txt"
javac -source 11 -target 11 -classpath "$ANDROID_JAR" -d "$BUILD/classes" @"$BUILD/sources.txt"
(cd "$BUILD/classes" && "$BT/d8" --lib "$ANDROID_JAR" --output "$BUILD/dex" $(find . -name '*.class'))
cp "$BUILD/base.apk" "$BUILD/unsigned.apk"
(cd "$BUILD/dex" && zip -q "$BUILD/unsigned.apk" classes.dex)
"$BT/zipalign" -f 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"
"$BT/apksigner" sign --ks "$HOME/.android/debug.keystore" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out "$OUT" "$BUILD/aligned.apk"
echo "$OUT"
