#!/bin/zsh
# 编译「買物.app」到 <项目根>/build/買物.app。装到 /Applications：ditto build/買物.app /Applications/買物.app
set -e
here=${0:A:h}
out=$here/../build
app="$out/買物.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$out/.modcache"

# 不指定 -target 时 swiftc 会按 SDK 版本定最低系统，可能比本机系统还新
swiftc -O -target arm64-apple-macos13.0 -module-cache-path "$out/.modcache" \
  -o "$app/Contents/MacOS/Shopping" "$here/main.swift"
cp "$here/Info.plist" "$app/Contents/Info.plist"

iconset=$out/AppIcon.iconset
mkdir -p $iconset
# 图标：繁体单字 + 色线
swift -module-cache-path "$out/.modcache" "$here/make_char_icon.swift" 買 2F6B4F $out/icon-1024.png
sips -z 512 512 $out/icon-1024.png --out "$here/../web/icon-512.png" >/dev/null
sips -z 192 192 $out/icon-1024.png --out "$here/../web/icon-192.png" >/dev/null
for s in 16 32 128 256 512; do
  sips -z $s $s $out/icon-1024.png --out $iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) $out/icon-1024.png --out $iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns $iconset -o "$app/Contents/Resources/AppIcon.icns"

codesign --force --sign - "$app"
echo "built: $app"
