#!/usr/bin/env bash
# Installs the local brain for Jarvis: Ollama (user-level, no sudo) + qwen3.8:27b.
# Speech models (Whisper, Kokoro) are fetched by the Python side on first use.
set -euo pipefail
VER="${OLLAMA_VERSION:-v0.40.8}"
MODEL="${LOCAL_MODEL:-qwen3.8:27b}"
OPT="$HOME/.local/opt/ollama"
if [ ! -x "$OPT/bin/ollama" ]; then
  echo "==> downloading Ollama $VER"
  mkdir -p "$OPT" && cd "$HOME/.local/opt"
  curl -fL --retry 3 -o ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$VER/ollama-linux-amd64.tar.zst"
  tar --zstd -xf ollama.tar.zst -C "$OPT" && rm -f ollama.tar.zst
fi
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$OPT/bin/ollama" "$HOME/.local/bin/ollama"
cat > "$HOME/.config/systemd/user/ollama.service" <<'U'
[Unit]
Description=Ollama (local LLM server for Jarvis)
After=network-online.target

[Service]
ExecStart=%h/.local/opt/ollama/bin/ollama serve
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_KEEP_ALIVE=30m
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Nice=5
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
U
systemctl --user daemon-reload
systemctl --user enable --now ollama.service
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
echo "==> ollama $(ollama --version | tail -1); pulling $MODEL"
ollama pull "$MODEL"
echo "==> DONE"
