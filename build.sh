#!/usr/bin/env bash
# Builds dist/factory-map_<version>.zip from mod/factory-map (the version comes from info.json).
set -euo pipefail
cd "$(dirname "$0")"
v=$(python3 -c "import json;print(json.load(open('mod/factory-map/info.json'))['version'])")
rm -rf "build/factory-map_$v" && mkdir -p "build/factory-map_$v" dist
cp -r mod/factory-map/. "build/factory-map_$v/"
python3 -c "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', 'build', sys.argv[2])" "dist/factory-map_$v" "factory-map_$v"
rm -rf build
echo "dist/factory-map_$v.zip"
