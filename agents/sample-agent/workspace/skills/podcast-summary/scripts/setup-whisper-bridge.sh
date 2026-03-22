#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# setup-whisper-bridge.sh
#
# Clones, builds, and installs whisper.cpp as a persistent macOS LaunchAgent.
# Safe to re-run: skips steps that are already complete.
#
# Usage:
#   setup-whisper-bridge.sh [--model-dir DIR] [--port PORT] [--whisper-dir DIR]
# ---------------------------------------------------------------------------

# ── Defaults ────────────────────────────────────────────────────────────────
MODEL_DIR="$HOME/whisper-models"
PORT=18797
WHISPER_DIR="$HOME/whisper.cpp"

PLIST_NAME="com.ironclaw.whisper-bridge"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$PLIST_NAME.plist"
PLIST_SRC="$(dirname "$0")/launchagents/$PLIST_NAME.plist"

SMALL_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
LARGE_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"

# ── Colors (stdout only) ─────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

ok()   { printf "${GREEN}[ok]${RESET}  %s\n" "$*"; }
skip() { printf "${YELLOW}[skip]${RESET} %s\n" "$*"; }
info() { printf "      %s\n" "$*"; }
die()  { printf "${RED}[error]${RESET} %s\n" "$*" >&2; exit 1; }

# ── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model-dir)   MODEL_DIR="$2";   shift 2 ;;
        --port)        PORT="$2";        shift 2 ;;
        --whisper-dir) WHISPER_DIR="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

# ── Prerequisite checks ──────────────────────────────────────────────────────
check_bin() {
    command -v "$1" &>/dev/null || die "'$1' is required but not installed. Install with: $2"
}

check_bin cmake  "brew install cmake"
check_bin git    "brew install git"
check_bin ffmpeg "brew install ffmpeg"

# Accept either curl or wget for downloads
if command -v curl &>/dev/null; then
    DOWNLOADER="curl"
elif command -v wget &>/dev/null; then
    DOWNLOADER="wget"
else
    die "'curl' or 'wget' is required but neither is installed."
fi

# ── Helper: download a file if not already present ───────────────────────────
download_if_missing() {
    local url="$1"
    local dest="$2"
    local label="$3"

    if [[ -f "$dest" ]]; then
        skip "$label already present — skipping download"
        return
    fi

    info "Downloading $label (~$(echo "$url" | grep -o 'large\|small' || echo '?')...)  (progress on stderr)"
    if [[ "$DOWNLOADER" == "curl" ]]; then
        curl -L --progress-bar -o "$dest" "$url" >&2
    else
        wget -q --show-progress -O "$dest" "$url" >&2
    fi
    ok "Downloaded $label"
}

# ── Step 1: Clone or update whisper.cpp ──────────────────────────────────────
printf "\n==> Step 1: whisper.cpp source\n"
if [[ ! -d "$WHISPER_DIR" ]]; then
    info "Cloning whisper.cpp into $WHISPER_DIR"
    git clone https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR" >&2
    ok "Cloned whisper.cpp"
else
    info "Updating existing clone at $WHISPER_DIR"
    git -C "$WHISPER_DIR" pull >&2
    ok "Updated whisper.cpp"
fi

# ── Step 2: Build whisper-server ─────────────────────────────────────────────
printf "\n==> Step 2: Build whisper-server\n"
info "Building with Metal GPU acceleration (-DWHISPER_METAL=ON) — this may take a few minutes"
mkdir -p "$WHISPER_DIR/build"
(
    cd "$WHISPER_DIR/build"
    cmake .. -DWHISPER_METAL=ON >&2
    cmake --build . --config Release -j4 >&2
)
ok "Built whisper-server at $WHISPER_DIR/build/bin/whisper-server"

# ── Step 3: Download models ───────────────────────────────────────────────────
printf "\n==> Step 3: Models\n"
mkdir -p "$MODEL_DIR"
download_if_missing "$SMALL_MODEL_URL" "$MODEL_DIR/ggml-small.en.bin" "ggml-small.en.bin (~150 MB)"
download_if_missing "$LARGE_MODEL_URL" "$MODEL_DIR/ggml-large-v3.bin" "ggml-large-v3.bin (~3 GB)"

# ── Step 4: Install LaunchAgent plist ────────────────────────────────────────
printf "\n==> Step 4: LaunchAgent\n"

[[ -f "$PLIST_SRC" ]] || die "Plist template not found at: $PLIST_SRC"

mkdir -p "$LAUNCH_AGENTS_DIR"

# Substitute placeholders — MODEL_DIR is baked into the plist via WHISPER_DIR;
# the plist always points at <whisper-dir>/models/ggml-small.en.bin for startup.
sed \
    -e "s|__WHISPER_DIR__|$WHISPER_DIR|g" \
    -e "s|__PORT__|$PORT|g" \
    "$PLIST_SRC" > "$PLIST_DEST"

# Unload first if already registered (ignore errors — it may not be loaded yet)
if launchctl list | grep -q "$PLIST_NAME" 2>/dev/null; then
    info "Unloading existing LaunchAgent before reload"
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

launchctl load "$PLIST_DEST"
ok "Installed and loaded LaunchAgent: $PLIST_DEST"

# ── Step 5: Smoke test ───────────────────────────────────────────────────────
printf "\n==> Step 5: Smoke test\n"
info "Waiting 2 seconds for server to start..."
sleep 2

if curl -s --max-time 5 "http://localhost:$PORT/" >/dev/null 2>&1; then
    ok "whisper-server is responding at http://localhost:$PORT/"
else
    printf "${YELLOW}[warn]${RESET} Server not yet responding at http://localhost:%s/\n" "$PORT"
    info "It may still be loading the model. Check: cat /tmp/whisper-bridge.log"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
printf "\n${GREEN}Setup complete.${RESET}\n"
printf "  whisper.cpp dir : %s\n" "$WHISPER_DIR"
printf "  Models dir      : %s\n" "$MODEL_DIR"
printf "  Bridge port     : %s\n" "$PORT"
printf "  LaunchAgent     : %s\n" "$PLIST_DEST"
printf "  Logs            : /tmp/whisper-bridge.log\n"
printf "\nTo check bridge status:\n"
printf "  curl http://localhost:%s/\n" "$PORT"
printf "  launchctl list | grep whisper\n"
printf "  cat /tmp/whisper-bridge.log\n"
