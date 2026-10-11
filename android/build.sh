#!/usr/bin/env bash
# Build and sign the Alberich widget APK with the SDK in /opt/android-sdk. No Gradle needed.
set -euo pipefail
SDK=/opt/android-sdk; BT=$SDK/build-tools/34.0.0; JAR=$SDK/platforms/android-34/android.jar
cd "$(dirname "$0")"
KS_DIR="$HOME/.alberich-android"; KS="$KS_DIR/alberich.keystore"
if [ ! -f "$KS" ]; then
  mkdir -p "$KS_DIR"; chmod 700 "$KS_DIR"
  head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$KS_DIR/ks.pass"; chmod 600 "$KS_DIR/ks.pass"
  keytool -genkeypair -keystore "$KS" -storepass "$(cat "$KS_DIR/ks.pass")" -keypass "$(cat "$KS_DIR/ks.pass")" -alias alberich \
    -keyalg RSA -keysize 2048 -validity 9125 -dname "CN=Alberich widget, O=Home" >/dev/null 2>&1
fi
PASS="$(cat "$KS_DIR/ks.pass")"
OUT=build; rm -rf $OUT; mkdir -p $OUT/gen $OUT/classes
$BT/aapt2 compile --dir res -o $OUT/res.zip
$BT/aapt2 link -o $OUT/base.apk -I $JAR --manifest AndroidManifest.xml --min-sdk-version 26 --target-sdk-version 34 \
  --version-code "${VERSION_CODE:-1}" --version-name "${VERSION_NAME:-1.0}" -A assets --java $OUT/gen $OUT/res.zip
javac -g:none -source 8 -target 8 -Xlint:-options -classpath $JAR -d $OUT/classes $(find src $OUT/gen -name '*.java')
$BT/d8 --release --min-api 26 --lib $JAR --output $OUT $(find $OUT/classes -name '*.class')
python3 -c "import zipfile;z=zipfile.ZipFile('$OUT/base.apk','a',zipfile.ZIP_DEFLATED);z.write('$OUT/classes.dex','classes.dex');z.close()"
$BT/zipalign -f -p 4 $OUT/base.apk $OUT/aligned.apk
$BT/apksigner sign --ks "$KS" --ks-pass "pass:$PASS" --key-pass "pass:$PASS" --out $OUT/alberich-widget.apk $OUT/aligned.apk
$BT/apksigner verify --print-certs $OUT/alberich-widget.apk | head -2
ls -la $OUT/alberich-widget.apk
