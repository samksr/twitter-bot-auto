#!/data/data/com.termux/files/usr/bin/bash
echo "🦅 Installing V46 Sentinel Dependencies..."
pkg update -y && pkg upgrade -y
pkg install python rust binutils -y
export CARGO_BUILD_TARGET=aarch64-linux-android
pip install --upgrade pip
pip install -r requirements.txt --no-cache-dir
mkdir -p data/logs data/backups
echo "✅ Setup Complete! Run './start.sh' to begin."
