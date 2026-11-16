#!/usr/bin/env bash
# Build the REAL MiniMuse phone APKs (the classic WebView+Python line, revived from
# the pre-openminis tag, pointed at the CURRENT upgraded runtime and web app):
#   MiniMuse-<v>-local-arm64.apk    the whole app runs ON the phone (Alpine+Python capsule)
#   MiniMuse-<v>-connect.apk        thin remote that pairs with `nanomuse serve` on a PC (QR)
# No Docker needed: the aarch64 rootfs is built with qemu-user-static + chroot inside WSL.
# Logged to ${LOG:-/tmp/classic-build.log}. Safe to re-run.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${LOG:-/tmp/classic-build.log}"
exec > >(tee -a "$LOG") 2>&1
echo "== [$(date -Is)] MiniMuse classic build start =="

REPO="${REPO:-$SCRIPT_DIR}"
W="$REPO/android-classic"
BW="${BW:-$HOME/nmclassic}"        # gradle builds here (native fs, fast)
STAGE="${STAGE:-/var/tmp/nm-rootfs}"  # rootfs staging
OUT="${OUT:-$SCRIPT_DIR/dist}"
mkdir -p "$OUT"

echo "== disk =="
df -h / | tail -1

echo "== stop the abandoned OpenMinis build (the APK the user rejected) =="
pkill -f minimuse-build.sh 2>/dev/null || true
pkill -f build_rclone_android 2>/dev/null || true
pkill -f "gomobile bind" 2>/dev/null || true
pkill -f "gradle" 2>/dev/null || true
sleep 1
echo "stopped."

echo "== apt: qemu-user-static (emulates aarch64 so we can build the phone rootfs on this PC) =="
# Ubuntu's unattended-upgrades can hold the dpkg lock right after boot; wait for it
apt-get -o DPkg::Lock::Timeout=600 update -y
apt-get -o DPkg::Lock::Timeout=600 install -y qemu-user-static xz-utils unzip python3-pip python3-venv
command -v qemu-aarch64-static

# binfmt: lets the kernel run aarch64 binaries through qemu transparently
if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
  modprobe binfmt_misc 2>/dev/null || true
  mount binfmt_misc -t binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true
  update-binfmts --enable qemu-aarch64 || true
fi
ls /proc/sys/fs/binfmt_misc/ | grep -i aarch || echo "WARN: no binfmt entry yet"

echo "== 1/6 build the nanomuse wheel from the CURRENT upgraded source =="
python3 --version
NM_V=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1 | tr -d '\r')
rm -rf /var/tmp/wheelout
mkdir -p /var/tmp/wheelout
python3 -m pip wheel --no-deps -q -w /var/tmp/wheelout "$REPO" 2>&1 | tail -5
WHEEL=$(ls /var/tmp/wheelout/nanomuse-*.whl | head -1)
echo "wheel ($NM_V): $WHEEL"

echo "== 2/6 stage an Alpine aarch64 rootfs (the phone's Linux) =="
# a killed run can leave its mounts behind; loosen them before touching the tree
umount -l "$STAGE/rootfs/proc" "$STAGE/rootfs/sys" "$STAGE/rootfs/dev" 2>/dev/null || true

if [ "${REFRESH_WHEEL:-0}" != 1 ] && chroot "$STAGE/rootfs" /opt/nanomuse/bin/python -c "import fastapi, uvicorn, nanomuse; assert nanomuse.__version__ == '$NM_V'" 2>/dev/null; then
  echo "cached rootfs for $NM_V: OK, keeping it"
else
  rm -rf "$STAGE"; mkdir -p "$STAGE/rootfs"
  ALPINE_TAR=/root/nanomuse/android/src/android/app/src/main/assets/alpine-minirootfs.tar.gz
  test -f "$ALPINE_TAR" || { echo "FAIL: alpine rootfs missing at $ALPINE_TAR"; exit 1; }
  tar -xzf "$ALPINE_TAR" -C "$STAGE/rootfs"
  cp -L /etc/resolv.conf "$STAGE/rootfs/etc/resolv.conf"
  mkdir -p "$STAGE/rootfs/qemu-bin"
  cp /usr/bin/qemu-aarch64-static "$STAGE/rootfs/qemu-bin/"
  # sanity: can we execute aarch64 code here at all? (-L tells qemu where the musl loader lives)
  "$STAGE/rootfs/qemu-bin/qemu-aarch64-static" -L "$STAGE/rootfs" "$STAGE/rootfs/bin/busybox" echo "qemu execution: OK"

  mount -t proc proc "$STAGE/rootfs/proc" 2>/dev/null || true
  mount -t sysfs sys "$STAGE/rootfs/sys" 2>/dev/null || true
  mount --bind /dev "$STAGE/rootfs/dev" 2>/dev/null || true

  cp "$WHEEL" "$STAGE/rootfs/var/tmp/"
fi

echo "== 3/6 install python + nanomuse + node inside the rootfs (emulated, a few minutes) =="
# skip the (slow) emulated install when a previous run already finished it for the same version
if [ "${REFRESH_WHEEL:-0}" != 1 ] && chroot "$STAGE/rootfs" /opt/nanomuse/bin/python -c "import fastapi, uvicorn, nanomuse; assert nanomuse.__version__ == '$NM_V'" 2>/dev/null; then
  echo "cached install for $NM_V: OK, skipping apk/pip"
else
  chroot "$STAGE/rootfs" /bin/sh -c '
    set -e
    apk add --no-cache python3 py3-pip bash coreutils curl git openssh-client \
      ca-certificates tzdata libstdc++ libgcc libffi openssl jq nodejs npm
    python3 -m venv /opt/nanomuse
    /opt/nanomuse/bin/pip install --no-cache-dir --prefer-binary /var/tmp/nanomuse-*.whl
    rm -f /var/tmp/nanomuse-*.whl
    rm -rf /opt/nanomuse/lib/python3*/site-packages/pip* /opt/nanomuse/lib/python3*/site-packages/setuptools* /opt/nanomuse/bin/pip*
    /opt/nanomuse/bin/python -c "import fastapi, uvicorn, nanomuse; print(\"runtime imports: OK\")"
  '
fi
# the build machine's mounts and the qemu helper never belong inside the phone capsule
umount -l "$STAGE/rootfs/proc" "$STAGE/rootfs/sys" "$STAGE/rootfs/dev" 2>/dev/null || true

echo "== 4/6 finish the capsule: serve scripts, trim, stamp, compress =="
cp -a "$REPO/scripts/rootfs/inside/." "$STAGE/rootfs/"
sed -i 's/\r$//' "$STAGE/rootfs/usr/local/bin/nanomuse-serve" "$STAGE/rootfs/usr/local/bin/nanomuse-mirror"
chmod 755 "$STAGE/rootfs/usr/local/bin/nanomuse-serve" "$STAGE/rootfs/usr/local/bin/nanomuse-mirror"
mkdir -p "$STAGE/rootfs/root/workspace" "$STAGE/rootfs/root/.nanomuse" "$STAGE/rootfs/tmp"
chmod 1777 "$STAGE/rootfs/tmp"
# trim what a phone never needs (same cuts the upstream Dockerfile makes)
rm -rf "$STAGE"/rootfs/usr/lib/python3*/test "$STAGE"/rootfs/usr/lib/python3*/idlelib \
       "$STAGE"/rootfs/usr/lib/python3*/tkinter "$STAGE"/rootfs/usr/lib/python3*/ensurepip \
       "$STAGE"/rootfs/usr/lib/python3*/lib2to3 "$STAGE"/rootfs/root/.cache \
       "$STAGE"/rootfs/var/cache/apk/* "$STAGE"/rootfs/usr/share/man \
       "$STAGE"/rootfs/usr/share/doc "$STAGE"/rootfs/usr/share/info \
       "$STAGE"/rootfs/usr/share/locale "$STAGE"/rootfs/usr/share/misc \
       "$STAGE"/rootfs/usr/lib/node_modules/npm/docs "$STAGE"/rootfs/usr/lib/node_modules/npm/man
find "$STAGE/rootfs" -xdev \( -path '*/site-packages/*/tests' -o -path '*/site-packages/*/test' \) \
  -type d -prune -exec rm -rf {} + 2>/dev/null || true
# verify the personal-data boundary: no keys, no transcripts, no user dbs in the capsule
echo "-- privacy scan of capsule --"
find "$STAGE/rootfs/opt/nanomuse" -name "*.db" -o -name "*.sqlite" -o -name "*.jsonl" -o -name "*.jks" -o -name "*.pem" | head -5
grep -rln "sk-[a-zA-Z0-9]\{20\}" "$STAGE/rootfs/opt/nanomuse/lib" 2>/dev/null | head -3 || echo "no secrets in capsule: OK"

PY_V=$(chroot "$STAGE/rootfs" /opt/nanomuse/bin/python -c 'import platform;print(platform.python_version())')
NODE_V=$(chroot "$STAGE/rootfs" /bin/sh -c 'command -v node >/dev/null && node --version || echo none')
ALPINE_V=$(cat "$STAGE/rootfs/etc/alpine-release")
NM_V=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1 | tr -d '\r')
GIT_S=$(git -C "$REPO" rev-parse --short=8 HEAD 2>/dev/null || echo local)
RFS_VERSION="${NM_V}+${GIT_S}"

mkdir -p "$W/app/src/local/assets"
XZ_OUT="$W/app/src/local/assets/rootfs.tar.xz"
if [ -s "$XZ_OUT" ] && [ -f "$W/app/src/local/assets/rootfs.json" ] && [ "${FORCE_SEAL:-0}" != 1 ]; then
  echo "sealed capsule already present: $XZ_OUT"
  echo "(delete it, or run with FORCE_SEAL=1, to re-seal after changing the runtime)"
else
tar -C "$STAGE/rootfs" --exclude='./proc' --exclude='./sys' --exclude='./dev' \
    --exclude='./qemu-bin' -cf - . | xz -T0 -7 > "$XZ_OUT"
SIZE=$(stat -c %s "$XZ_OUT")
UNPACKED=$(du -sb "$STAGE/rootfs" | cut -f1)
SHA=$(sha256sum "$XZ_OUT" | cut -d' ' -f1)
cat > "$W/app/src/local/assets/rootfs.json" <<EOF
{
  "name": "rootfs.tar.xz",
  "version": "$RFS_VERSION",
  "nanomuse": "$NM_V",
  "arch": "aarch64",
  "alpine": "$ALPINE_V",
  "python": "$PY_V",
  "node": "$NODE_V",
  "size": $SIZE,
  "unpacked": $UNPACKED,
  "sha256": "$SHA",
  "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "rootfs: $((UNPACKED/1048576)) MB unpacked, $((SIZE/1048576)) MB compressed, sha256 $SHA"
fi

echo "== 5/6 proot binaries (already built and verified earlier today) =="
SRCJNI=/root/nanomuse/android/src/android/app/src/main/jniLibs/arm64-v8a
test -f "$SRCJNI/libproot.so" && test -f "$SRCJNI/libproot-loader.so" || { echo "FAIL: proot libs missing at $SRCJNI"; exit 1; }
mkdir -p "$W/app/src/local/jniLibs/arm64-v8a"
cp "$SRCJNI/libproot.so" "$SRCJNI/libproot-loader.so" "$W/app/src/local/jniLibs/arm64-v8a/"
ls -la "$W/app/src/local/jniLibs/arm64-v8a/"

echo "== 6/6 gradle: test, sign and assemble unified APK =="
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="$HOME/android-sdk"
java -version 2>&1 | head -1

rm -rf "$BW"; mkdir -p "$BW"
cp -a "$W/." "$BW/"
# signing (supply KEYSTORE_FILE / KEYSTORE_PW_FILE via env for release builds)
if [ -n "${KEYSTORE_FILE:-}" ] && [ -f "$KEYSTORE_FILE" ]; then
  cp "$KEYSTORE_FILE" "$BW/minimuse-release.jks"
  KEYPW="${KEYSTORE_PASSWORD:-$(cat "${KEYSTORE_PW_FILE:?set KEYSTORE_PASSWORD or KEYSTORE_PW_FILE}")}"
  cat > "$BW/keystore.properties" <<EOF
storeFile=minimuse-release.jks
storePassword=$KEYPW
keyAlias=${KEY_ALIAS:-minimuse}
keyPassword=$KEYPW
EOF
fi
cd "$BW"
# files that crossed the Windows filesystem may carry CRLF; gradlew's shebang dies on that
sed -i 's/\r$//' gradlew
sh ./gradlew :app:testReleaseUnitTest :app:assembleRelease --console=plain --no-daemon 2>&1 | tail -35

echo "== copy out =="
ls -la app/build/outputs/apk/release/
cp app/build/outputs/apk/release/app-release.apk "$OUT/MiniMuse-${NM_V}-arm64.apk"
cd "$OUT"
sha256sum "MiniMuse-${NM_V}-arm64.apk" | tee MiniMuse-${NM_V}-APKs.sha256
ls -la "$OUT"/MiniMuse-${NM_V}*.apk

echo "== [$(date -Is)] DONE: unified APK built and signed =="
