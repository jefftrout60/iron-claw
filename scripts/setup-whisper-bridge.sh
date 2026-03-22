#!/usr/bin/env bash
# setup-whisper-bridge.sh — Build whisper.cpp from source, download models,
# and install the whisper-server LaunchAgent on macOS.
#
# Usage:
#   ./scripts/setup-whisper-bridge.sh [options]
#
# Options:
#   --model-dir PATH   Directory to clone whisper.cpp into (default: ~/whisper.cpp)
#   --port PORT        Port for whisper-server to listen on (default: 18797)
#   --help             Show this help and exit
#
# What this script does (idempotent — safe to re-run):
#   1. Checks prerequisites: cmake, git, ffmpeg
#   2. Clones whisper.cpp if not already present
#   3. Builds whisper-server with cmake (Metal GPU on Apple Silicon)
#   4. Downloads ggml-small.en.bin (default model)
#   5. Downloads ggml-large-v3.bin (health shows: Attia, Huberman, Rhonda Patrick)
#   6. Installs LaunchAgent plist from template (substitutes real paths)
#   7. Loads LaunchAgent via launchctl
#   8. Prints verification instructions
#
# After install, whisper-server is always-on at 0.0.0.0:<PORT>.
# Docker containers reach it via host.docker.internal:<PORT>.

set -e

# --- Defaults ---
MODEL_DIR="$HOME/whisper.cpp"
PORT=18797
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_TMPL="$SCRIPT_DIR/launchagents/com.ironclaw.whisper-bridge.plist.tmpl"
LAUNCHAGENT_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCHAGENT_DIR/com.ironclaw.whisper-bridge.plist"
LOGS_DIR="$HOME/Library/Logs"

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-dir)
      MODEL_DIR="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --help|-h)
      grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

# --- Helpers ---
step() { echo ""; echo "==> $*"; }
info() { echo "    $*"; }
ok()   { echo "    [ok] $*"; }
fail() { echo ""; echo "ERROR: $*" >&2; exit 1; }

# --- Prerequisite checks ---
step "Checking prerequisites"

missing=()
for bin in cmake git ffmpeg; do
  if ! command -v "$bin" &>/dev/null; then
    missing+=("$bin")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo ""
  echo "ERROR: Missing required tools: ${missing[*]}" >&2
  echo ""
  echo "Install with Homebrew:" >&2
  for m in "${missing[@]}"; do
    echo "  brew install $m" >&2
  done
  echo ""
  echo "After installing, re-run this script." >&2
  exit 1
fi

ok "cmake: $(cmake --version | head -1)"
ok "git: $(git --version)"
ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"

# --- Step 1: Clone whisper.cpp ---
step "whisper.cpp source ($MODEL_DIR)"

if [[ -d "$MODEL_DIR/.git" ]]; then
  ok "Already cloned — skipping git clone"
else
  info "Cloning https://github.com/ggerganov/whisper.cpp ..."
  git clone https://github.com/ggerganov/whisper.cpp "$MODEL_DIR"
  ok "Cloned"
fi

# --- Step 2: Build whisper-server ---
WHISPER_SERVER_BIN="$MODEL_DIR/build/bin/whisper-server"

step "Building whisper-server ($WHISPER_SERVER_BIN)"

if [[ -f "$WHISPER_SERVER_BIN" ]]; then
  ok "Binary already exists — skipping build"
  info "To force a rebuild: rm -rf $MODEL_DIR/build && re-run this script"
else
  info "Configuring with cmake (Release + Metal on Apple Silicon) ..."
  cmake -G Ninja -B "$MODEL_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -S "$MODEL_DIR"

  info "Building whisper-server target ..."
  cmake --build "$MODEL_DIR/build" --config Release --target whisper-server

  if [[ ! -f "$WHISPER_SERVER_BIN" ]]; then
    fail "Build completed but binary not found at $WHISPER_SERVER_BIN"
  fi
  ok "Built: $WHISPER_SERVER_BIN"
fi

# --- Step 3: Download models ---
SMALL_MODEL="$MODEL_DIR/models/ggml-small.en.bin"
LARGE_MODEL="$MODEL_DIR/models/ggml-large-v3.bin"

step "Downloading model: ggml-small.en (default model)"
if [[ -f "$SMALL_MODEL" ]]; then
  ok "Already downloaded — $SMALL_MODEL"
else
  info "Downloading ggml-small.en.bin (this may take a minute) ..."
  bash "$MODEL_DIR/models/download-ggml-model.sh" small.en
  if [[ ! -f "$SMALL_MODEL" ]]; then
    fail "Download failed — $SMALL_MODEL not found"
  fi
  ok "Downloaded: $SMALL_MODEL"
fi

step "Downloading model: ggml-large-v3 (health shows: Attia, Huberman, Rhonda Patrick)"
if [[ -f "$LARGE_MODEL" ]]; then
  ok "Already downloaded — $LARGE_MODEL"
else
  info "Downloading ggml-large-v3.bin (~3 GB — this will take several minutes) ..."
  bash "$MODEL_DIR/models/download-ggml-model.sh" large-v3
  if [[ ! -f "$LARGE_MODEL" ]]; then
    fail "Download failed — $LARGE_MODEL not found"
  fi
  ok "Downloaded: $LARGE_MODEL"
fi

# --- Step 4: Install LaunchAgent ---
step "Installing LaunchAgent plist"

if [[ ! -f "$PLIST_TMPL" ]]; then
  fail "Plist template not found: $PLIST_TMPL"
fi

mkdir -p "$LAUNCHAGENT_DIR"
mkdir -p "$LOGS_DIR"

CURRENT_USER="$(whoami)"

# Substitute placeholders in template → destination plist
sed \
  -e "s|WHISPER_SERVER_BIN|$WHISPER_SERVER_BIN|g" \
  -e "s|WHISPER_MODEL_PATH|$SMALL_MODEL|g" \
  -e "s|WHISPER_PORT|$PORT|g" \
  -e "s|CURRENT_USER|$CURRENT_USER|g" \
  "$PLIST_TMPL" > "$PLIST_DEST"

ok "Wrote: $PLIST_DEST"
info "  whisper-server: $WHISPER_SERVER_BIN"
info "  model:          $SMALL_MODEL"
info "  port:           $PORT"
info "  log:            $LOGS_DIR/whisper-bridge.log"

# --- Step 5: Load LaunchAgent ---
step "Loading LaunchAgent"

# Unload first if already loaded (ignore errors — may not be loaded yet)
launchctl unload "$PLIST_DEST" 2>/dev/null || true

launchctl load "$PLIST_DEST"
ok "LaunchAgent loaded: com.ironclaw.whisper-bridge"

# --- Verification instructions ---
echo ""
echo "======================================================================"
echo " Whisper bridge installed and started"
echo "======================================================================"
echo ""
echo " Port:   $PORT"
echo " Model:  $SMALL_MODEL"
echo " Log:    $LOGS_DIR/whisper-bridge.log"
echo ""
echo " Verify the server is running:"
echo "   curl http://localhost:$PORT/"
echo "   # Should return HTML test UI from whisper-server"
echo ""
echo " Check health:"
echo "   ./scripts/check-whisper-bridge.sh"
echo ""
echo " View logs:"
echo "   tail -f $LOGS_DIR/whisper-bridge.log"
echo ""
echo " Test transcription (MP3 via --convert flag):"
echo "   curl http://localhost:$PORT/inference \\"
echo "        -F file=@/path/to/test.mp3 \\"
echo "        -F response_format=json"
echo ""
echo " Manage:"
echo "   launchctl unload $PLIST_DEST   # stop"
echo "   launchctl load   $PLIST_DEST   # start"
echo "   launchctl list | grep whisper-bridge  # check status"
echo ""
echo " Models downloaded to $MODEL_DIR/models/:"
[[ -f "$SMALL_MODEL" ]] && echo "   [x] ggml-small.en.bin  (default — fast, English-only)"
[[ -f "$LARGE_MODEL" ]] && echo "   [x] ggml-large-v3.bin  (health shows — slower, higher accuracy)"
echo ""
echo " The server auto-restarts on failure (KeepAlive=true)."
echo " It starts automatically on next login."
echo "======================================================================"
