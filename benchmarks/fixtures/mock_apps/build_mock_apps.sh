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

build_app mock_shop edu.agentos.mockshop
build_app mock_payment edu.agentos.mockpayment

echo "$OUT/edu.agentos.mockshop.apk"
echo "$OUT/edu.agentos.mockpayment.apk"
