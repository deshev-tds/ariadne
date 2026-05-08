#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  resolved_path="$(readlink -f "$SCRIPT_PATH" 2>/dev/null || true)"
  [[ -n "$resolved_path" ]] && SCRIPT_PATH="$resolved_path"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
PATCH_FILE="${ARIADNE_LLAMA_PATCH_FILE:-$SCRIPT_DIR/patches/0001-server-allow-streamed-tool-calls-with-content-logprobs.patch}"

STORE="${ARIADNE_LLAMA_STORE:-$HOME/.local/opt/ariadne-llama}"
BUILDS_DIR="$STORE/builds"
SOURCES_DIR="$STORE/sources"
WORK_DIR="$STORE/work"
LOG_DIR="${ARIADNE_LLAMA_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ariadne-llama-backend/logs}"
LOCK_FILE="$STORE/.promotion.lock"

DEFAULT_TOOLBOX="${ARIADNE_LLAMA_TOOLBOX:-llama-rocm-7.2.2}"
DEFAULT_CANARY_PORT="${ARIADNE_LLAMA_CANARY_PORT:-1235}"
PRODUCTION_PORT="${ARIADNE_LLAMA_PRODUCTION_PORT:-1234}"

PINNED_REPO="${ARIADNE_LLAMA_PINNED_REPO:-https://github.com/am17an/llama.cpp.git}"
PINNED_BRANCH="${ARIADNE_LLAMA_PINNED_BRANCH:-mtp-clean}"
PINNED_REF="${ARIADNE_LLAMA_PINNED_REF:-5d5f1b46e4f56885801c86363d4677a5f72f83af}"
PINNED_SEED_SOURCE="${ARIADNE_LLAMA_PINNED_SEED_SOURCE:-$HOME/.local/src/llama.cpp-pr22673-ariadne}"

MTP_PR_REPO="${ARIADNE_LLAMA_MTP_PR_REPO:-https://github.com/am17an/llama.cpp.git}"
MTP_PR_BRANCH="${ARIADNE_LLAMA_MTP_PR_BRANCH:-mtp-clean}"

MAIN_REPO="${ARIADNE_LLAMA_MAIN_REPO:-https://github.com/ggml-org/llama.cpp.git}"
MAIN_BRANCH="${ARIADNE_LLAMA_MAIN_BRANCH:-master}"

MODELS_INI="${ARIADNE_LLAMA_MODELS_INI:-$HOME/models/models.ini}"
RUN_LLAMA="${ARIADNE_RUN_LLAMA:-$HOME/models/run_llama.sh}"
DUAL_MODELS="${ARIADNE_LLAMA_DUAL_MODELS:-Qwen3.6-27B-Dense-MTP-Q6_K Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL}"
TEXT_MTP_MODEL="${ARIADNE_LLAMA_TEXT_MTP_MODEL:-Qwen3.6-27B-Dense-MTP-Q6_K}"
VISION_MODEL="${ARIADNE_LLAMA_VISION_MODEL:-Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL}"

CTX="${ARIADNE_LLAMA_CTX:-131072}"
MODELS_MAX_DUAL="${ARIADNE_LLAMA_MODELS_MAX_DUAL:-2}"
MODELS_MAX_TEXT="${ARIADNE_LLAMA_MODELS_MAX_TEXT:-1}"
BATCH="${ARIADNE_LLAMA_BATCH:-2048}"
UBATCH_SIZE="${ARIADNE_LLAMA_UBATCH_SIZE:-512}"
CACHE_K="${ARIADNE_LLAMA_CACHE_K:-q8_0}"
CACHE_V="${ARIADNE_LLAMA_CACHE_V:-q8_0}"

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

build_stamp() {
  date '+%Y%m%dT%H%M%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" >&2
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

init_store() {
  mkdir -p "$BUILDS_DIR" "$SOURCES_DIR" "$WORK_DIR" "$LOG_DIR"
}

usage() {
  cat <<'USAGE'
Usage:
  ariadne-llama-backend.sh status
  ariadne-llama-backend.sh list
  ariadne-llama-backend.sh build --lane pinned|mtp-pr|main [--toolbox NAME] [--ref SHA]
  ariadne-llama-backend.sh import-current [--source-bin PATH] [--source-tree PATH] [--build-id ID] [--promote]
  ariadne-llama-backend.sh smoke --candidate BUILD_ID [--profile 27b-text-mtp|dual] [--port PORT] [--toolbox NAME]
  ariadne-llama-backend.sh promote --candidate BUILD_ID
  ariadne-llama-backend.sh rollback [--to BUILD_ID]

Environment:
  ARIADNE_LLAMA_STORE              Backend store. Default: ~/.local/opt/ariadne-llama
  ARIADNE_LLAMA_TOOLBOX            Default toolbox. Default: llama-rocm-7.2.2
  ARIADNE_LLAMA_PINNED_REF         Known-good MTP ref.
  ARIADNE_LLAMA_PINNED_SEED_SOURCE Local seed clone used to cache pinned source.
USAGE
}

shell_quote() {
  printf '%q' "$1"
}

manifest_set() {
  local manifest="$1"
  local key="$2"
  local value="${3:-}"
  printf '%s=%q\n' "$key" "$value" >>"$manifest"
}

manifest_get() {
  local manifest="$1"
  local key="$2"
  [[ -f "$manifest" ]] || return 1
  awk -F= -v k="$key" '$1 == k { sub(/^[^=]*=/, ""); print; found=1 } END { exit found ? 0 : 1 }' "$manifest" \
    | tail -n 1 \
    | while IFS= read -r value; do
        # Values are written with printf %q; eval only this single assignment-like value.
        eval "printf '%s' $value"
      done
}

sha256_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    printf ''
    return 0
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    printf ''
  fi
}

short_ref() {
  printf '%s' "$1" | cut -c1-12
}

safe_id() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-'
}

toolbox_cmd() {
  if command -v toolbox >/dev/null 2>&1; then
    printf 'toolbox'
  elif command -v distrobox >/dev/null 2>&1; then
    printf 'distrobox'
  else
    return 1
  fi
}

detect_backend() {
  local text="$1"
  case "$text" in
    *vulkan-radv*) echo "vulkan-radv" ;;
    *vulkan-amdvlk*) echo "vulkan-amdvlk" ;;
    *rocm7-nightlies*) echo "rocm7-nightlies" ;;
    *rocm-6.4.4-rocwmma*) echo "rocm-6.4.4-rocwmma" ;;
    *rocm-6.4.4*) echo "rocm-6.4.4" ;;
    *rocm-7.2.2-pr21344*) echo "rocm-7.2.2-pr21344" ;;
    *rocm-7.2.2*) echo "rocm-7.2.2" ;;
    *rocm-7.2.1-pr21344*) echo "rocm-7.2.1-pr21344" ;;
    *rocm-7.2.1*) echo "rocm-7.2.1" ;;
    *rocm-7.2*) echo "rocm-7.2" ;;
    *rocm*) echo "rocm" ;;
    *) return 1 ;;
  esac
}

lane_config() {
  local lane="$1"
  case "$lane" in
    pinned)
      LANE_REPO="$PINNED_REPO"
      LANE_BRANCH="$PINNED_BRANCH"
      LANE_REF="$PINNED_REF"
      ;;
    mtp-pr)
      LANE_REPO="$MTP_PR_REPO"
      LANE_BRANCH="$MTP_PR_BRANCH"
      LANE_REF=""
      ;;
    main)
      LANE_REPO="$MAIN_REPO"
      LANE_BRANCH="$MAIN_BRANCH"
      LANE_REF=""
      ;;
    *)
      die "Unknown lane: $lane"
      ;;
  esac
}

resolve_ref() {
  local repo="$1"
  local branch="$2"
  local explicit_ref="${3:-}"

  if [[ -n "$explicit_ref" ]]; then
    printf '%s\n' "$explicit_ref"
    return 0
  fi

  need_cmd git
  local ref=""
  ref="$(git ls-remote --heads "$repo" "$branch" | awk 'NR == 1 {print $1}')"
  if [[ -z "$ref" ]]; then
    ref="$(git ls-remote "$repo" "$branch" | awk 'NR == 1 {print $1}')"
  fi
  [[ -n "$ref" ]] || die "Could not resolve ref for $repo $branch"
  printf '%s\n' "$ref"
}

ensure_source_cache() {
  local lane="$1"
  local repo="$2"
  local branch="$3"
  local ref="$4"
  local source_id source_dir tmp_dir
  source_id="$(safe_id "${lane}-$(short_ref "$ref")")"
  source_dir="$SOURCES_DIR/$source_id"

  if [[ -d "$source_dir/.git" ]] && git -C "$source_dir" cat-file -e "$ref^{commit}" 2>/dev/null; then
    printf '%s\n' "$source_dir"
    return 0
  fi

  rm -rf "$source_dir.tmp"
  tmp_dir="$source_dir.tmp"

  if [[ "$lane" == "pinned" && -d "$PINNED_SEED_SOURCE/.git" ]]; then
    log "Caching pinned source from local seed: $PINNED_SEED_SOURCE"
    git clone --recursive "$PINNED_SEED_SOURCE" "$tmp_dir"
  else
    log "Caching source from remote: $repo $branch"
    git clone --branch "$branch" --single-branch --recursive "$repo" "$tmp_dir"
  fi

  if ! git -C "$tmp_dir" cat-file -e "$ref^{commit}" 2>/dev/null; then
    git -C "$tmp_dir" fetch --no-tags "$repo" "$ref" || true
  fi
  git -C "$tmp_dir" checkout -f "$ref"
  git -C "$tmp_dir" submodule update --init --recursive

  rm -rf "$source_dir"
  mv "$tmp_dir" "$source_dir"
  printf '%s\n' "$source_dir"
}

apply_patch_queue() {
  local src="$1"
  [[ -f "$PATCH_FILE" ]] || die "Patch file not found: $PATCH_FILE"
  git -C "$src" apply --3way --check "$PATCH_FILE"
  git -C "$src" apply --3way "$PATCH_FILE"
}

backend_ld_library_path() {
  local prefix="$1"
  printf '%s:%s:%s:/opt/rocm/lib:/opt/rocm-7.2.2/lib' \
    "$prefix/lib64" "$prefix/lib" "$prefix/bin"
}

append_gpu_info() {
  local manifest="$1"
  if command -v rocminfo >/dev/null 2>&1; then
    manifest_set "$manifest" "ROCMINFO_AVAILABLE" "1"
    manifest_set "$manifest" "ROCM_AGENT_NAMES" "$(rocminfo 2>/dev/null | awk -F: '/Name:/ {gsub(/^[ \t]+/, "", $2); print $2}' | paste -sd ',' -)"
  else
    manifest_set "$manifest" "ROCMINFO_AVAILABLE" "0"
  fi
}

cmd_build_inside() {
  local src="" build_dir="" prefix="" toolbox="" manifest=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --src) src="${2:-}"; shift 2 ;;
      --build-dir) build_dir="${2:-}"; shift 2 ;;
      --prefix) prefix="${2:-}"; shift 2 ;;
      --toolbox) toolbox="${2:-}"; shift 2 ;;
      --manifest) manifest="${2:-}"; shift 2 ;;
      *) die "Unknown __build-inside argument: $1" ;;
    esac
  done

  [[ -d "$src" ]] || die "Source dir not found: $src"
  [[ -n "$build_dir" ]] || die "Missing --build-dir"
  [[ -n "$prefix" ]] || die "Missing --prefix"
  [[ -n "$toolbox" ]] || die "Missing --toolbox"
  [[ -n "$manifest" ]] || die "Missing --manifest"

  local backend
  backend="$(detect_backend "$toolbox")" || die "Could not detect backend from toolbox: $toolbox"

  mkdir -p "$build_dir" "$prefix/bin" "$prefix/lib" "$prefix/lib64"

  local cmake_args=(
    -S "$src"
    -B "$build_dir"
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_INSTALL_PREFIX="$prefix"
    -DGGML_RPC=ON
    -DLLAMA_BUILD_TESTS=OFF
    -DLLAMA_BUILD_EXAMPLES=ON
    -DLLAMA_BUILD_SERVER=ON
  )

  case "$backend" in
    vulkan-radv|vulkan-amdvlk)
      cmake_args+=(-DGGML_VULKAN=ON)
      ;;
    rocm7-nightlies)
      export ROCM_PATH="${ROCM_PATH:-/opt/rocm-7.0}"
      export HIP_PATH="${HIP_PATH:-$ROCM_PATH}"
      export HIP_CLANG_PATH="${HIP_CLANG_PATH:-$ROCM_PATH/llvm/bin}"
      export HIP_INCLUDE_PATH="${HIP_INCLUDE_PATH:-$ROCM_PATH/include}"
      export HIP_LIB_PATH="${HIP_LIB_PATH:-$ROCM_PATH/lib}"
      export HIP_DEVICE_LIB_PATH="${HIP_DEVICE_LIB_PATH:-$ROCM_PATH/lib/llvm/amdgcn/bitcode}"
      export PATH="$ROCM_PATH/bin:$ROCM_PATH/llvm/bin:$PATH"
      export LD_LIBRARY_PATH="$ROCM_PATH/lib:$ROCM_PATH/lib64:$ROCM_PATH/llvm/lib:${LD_LIBRARY_PATH:-}"
      export LIBRARY_PATH="$ROCM_PATH/lib:$ROCM_PATH/lib64:${LIBRARY_PATH:-}"
      export CPATH="$ROCM_PATH/include:${CPATH:-}"
      cmake_args+=(
        -DGGML_HIP=ON
        -DAMDGPU_TARGETS=gfx1151
        -DLLAMA_HIP_UMA=ON
      )
      ;;
    rocm-6*)
      export ROCM_PATH="${ROCM_PATH:-/usr}"
      if command -v hipconfig >/dev/null 2>&1; then
        export HIPCXX="$(hipconfig -l)/clang"
        export HIP_PATH="$(hipconfig -R)"
      fi
      cmake_args+=(
        -DGGML_HIP=ON
        -DAMDGPU_TARGETS=gfx1151
        -DLLAMA_HIP_UMA=ON
        -DGGML_CUDA_ENABLE_UNIFIED_MEMORY=ON
      )
      ;;
    rocm*)
      export ROCM_PATH="${ROCM_PATH:-/opt/rocm}"
      export HIP_PATH="${HIP_PATH:-$ROCM_PATH}"
      export HIP_CLANG_PATH="${HIP_CLANG_PATH:-$ROCM_PATH/llvm/bin}"
      export PATH="$ROCM_PATH/bin:$ROCM_PATH/llvm/bin:$PATH"
      cmake_args+=(
        -DGGML_HIP=ON
        "-DCMAKE_HIP_FLAGS=--rocm-path=$ROCM_PATH -mllvm --amdgpu-unroll-threshold-local=600"
        -DAMDGPU_TARGETS=gfx1151
        -DLLAMA_HIP_UMA=ON
        -DGGML_CUDA_ENABLE_UNIFIED_MEMORY=ON
        "-DROCM_PATH=$ROCM_PATH"
        "-DHIP_PATH=$HIP_PATH"
        -DHIP_PLATFORM=amd
      )
      ;;
    *)
      die "Unsupported backend: $backend"
      ;;
  esac

  manifest_set "$manifest" "BACKEND" "$backend"
  manifest_set "$manifest" "CMAKE_ARGS" "${cmake_args[*]}"

  cmake "${cmake_args[@]}"
  cmake --build "$build_dir" --config Release -- -j"$(nproc)"
  cmake --install "$build_dir" --config Release

  find "$build_dir" \( -type f -o -type l \) -name 'lib*.so*' -exec cp -P {} "$prefix/lib/" \; || true
  if [[ -d "$prefix/lib64" ]]; then
    find "$prefix/lib64" \( -type f -o -type l \) -name 'lib*.so*' -exec cp -P {} "$prefix/lib/" \; || true
  fi

  [[ -x "$prefix/bin/llama-server" ]] || die "Build did not produce $prefix/bin/llama-server"

  local ld_path version
  ld_path="$(backend_ld_library_path "$prefix")"
  version="$(LD_LIBRARY_PATH="$ld_path" "$prefix/bin/llama-server" --version 2>&1 | head -n 5 | paste -sd ';' -)"
  manifest_set "$manifest" "LLAMA_SERVER_VERSION" "$version"
  append_gpu_info "$manifest"
  manifest_set "$manifest" "BUILD_STATUS" "success"
}

cmd_build() {
  local lane="" toolbox="$DEFAULT_TOOLBOX" ref_override=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lane) lane="${2:-}"; shift 2 ;;
      --toolbox) toolbox="${2:-}"; shift 2 ;;
      --ref) ref_override="${2:-}"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown build argument: $1" ;;
    esac
  done
  [[ -n "$lane" ]] || die "build requires --lane"

  init_store
  need_cmd git
  local tb_cmd
  tb_cmd="$(toolbox_cmd)" || die "Missing toolbox/distrobox command"

  lane_config "$lane"
  local resolved_ref source_cache short build_id prefix work src build_dir manifest build_log patch_hash base_digest
  resolved_ref="$(resolve_ref "$LANE_REPO" "$LANE_BRANCH" "${ref_override:-$LANE_REF}")"
  short="$(short_ref "$resolved_ref")"
  source_cache="$(ensure_source_cache "$lane" "$LANE_REPO" "$LANE_BRANCH" "$resolved_ref")"

  build_id="$(safe_id "$(build_stamp)-${lane}-${short}-${toolbox}")"
  prefix="$BUILDS_DIR/$build_id"
  work="$WORK_DIR/$build_id"
  src="$work/src"
  build_dir="$work/build"
  manifest="$prefix/manifest.env"
  build_log="$LOG_DIR/build-${build_id}.log"

  [[ ! -e "$prefix" ]] || die "Build already exists: $build_id"
  mkdir -p "$prefix" "$work"

  log "Preparing candidate: $build_id"
  git clone --recursive "$source_cache" "$src"
  git -C "$src" checkout -f "$resolved_ref"
  git -C "$src" submodule update --init --recursive
  apply_patch_queue "$src"

  patch_hash="$(sha256_file "$PATCH_FILE")"
  base_digest="$(podman image inspect "docker.io/kyuz0/amd-strix-halo-toolboxes:${toolbox#llama-}" --format '{{.Digest}}' 2>/dev/null || true)"

  : >"$manifest"
  manifest_set "$manifest" "BUILD_ID" "$build_id"
  manifest_set "$manifest" "BUILD_METHOD" "build"
  manifest_set "$manifest" "LANE" "$lane"
  manifest_set "$manifest" "LLAMA_REPO" "$LANE_REPO"
  manifest_set "$manifest" "LLAMA_BRANCH" "$LANE_BRANCH"
  manifest_set "$manifest" "LLAMA_REF" "$resolved_ref"
  manifest_set "$manifest" "SOURCE_CACHE" "$source_cache"
  manifest_set "$manifest" "TOOLBOX" "$toolbox"
  manifest_set "$manifest" "BASE_IMAGE_DIGEST" "$base_digest"
  manifest_set "$manifest" "PATCH_FILE" "$PATCH_FILE"
  manifest_set "$manifest" "PATCH_SHA256" "$patch_hash"
  manifest_set "$manifest" "BUILD_STARTED_AT" "$(timestamp)"
  manifest_set "$manifest" "BUILD_LOG" "$build_log"

  log "Building inside toolbox: $toolbox"
  set +e
  "$tb_cmd" run -c "$toolbox" "$SCRIPT_DIR/ariadne-llama-backend.sh" __build-inside \
    --src "$src" \
    --build-dir "$build_dir" \
    --prefix "$prefix" \
    --toolbox "$toolbox" \
    --manifest "$manifest" 2>&1 | tee "$build_log"
  local build_status="${PIPESTATUS[0]}"
  set -e

  manifest_set "$manifest" "BUILD_FINISHED_AT" "$(timestamp)"
  if [[ "$build_status" -ne 0 ]]; then
    manifest_set "$manifest" "BUILD_STATUS" "failed"
    die "Candidate build failed: $build_id (log: $build_log)"
  fi

  log "Candidate built: $build_id"
  printf '%s\n' "$build_id"
}

cmd_import_current() {
  local source_bin="$PINNED_SEED_SOURCE/build-mtp/bin/llama-server"
  local source_tree="$PINNED_SEED_SOURCE"
  local lane="pinned"
  local ref="$PINNED_REF"
  local toolbox="$DEFAULT_TOOLBOX"
  local build_id=""
  local promote=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source-bin) source_bin="${2:-}"; shift 2 ;;
      --source-tree) source_tree="${2:-}"; shift 2 ;;
      --lane) lane="${2:-}"; shift 2 ;;
      --ref) ref="${2:-}"; shift 2 ;;
      --toolbox) toolbox="${2:-}"; shift 2 ;;
      --build-id) build_id="${2:-}"; shift 2 ;;
      --promote) promote=1; shift ;;
      *) die "Unknown import-current argument: $1" ;;
    esac
  done

  init_store
  [[ -x "$source_bin" ]] || die "Source llama-server is not executable: $source_bin"

  local short prefix manifest source_dir source_bin_dir patch_hash version ld_path
  short="$(short_ref "$ref")"
  if [[ -z "$build_id" ]]; then
    build_id="$(safe_id "$(build_stamp)-${lane}-${short}-${toolbox}-imported")"
  fi
  prefix="$BUILDS_DIR/$build_id"
  manifest="$prefix/manifest.env"
  [[ ! -e "$prefix" ]] || die "Build already exists: $build_id"

  mkdir -p "$prefix/bin" "$prefix/lib" "$prefix/lib64"
  cp -P "$source_bin" "$prefix/bin/llama-server"
  source_bin_dir="$(cd "$(dirname "$source_bin")" && pwd)"
  find "$source_bin_dir" -maxdepth 1 \( -type f -o -type l \) -name 'lib*.so*' -exec cp -P {} "$prefix/lib/" \; || true

  source_dir=""
  if [[ -d "$source_tree/.git" ]]; then
    local source_id="$SOURCES_DIR/$(safe_id "${lane}-${short}")"
    if [[ ! -d "$source_id/.git" ]]; then
      log "Caching imported source from: $source_tree"
      git clone --recursive "$source_tree" "$source_id"
      git -C "$source_id" checkout -f "$ref" || true
      git -C "$source_id" submodule update --init --recursive || true
    fi
    source_dir="$source_id"
  fi

  patch_hash="$(sha256_file "$PATCH_FILE")"
  ld_path="$(backend_ld_library_path "$prefix")"
  version="$(LD_LIBRARY_PATH="$ld_path" "$prefix/bin/llama-server" --version 2>&1 | head -n 5 | paste -sd ';' -)"

  : >"$manifest"
  manifest_set "$manifest" "BUILD_ID" "$build_id"
  manifest_set "$manifest" "BUILD_METHOD" "import-current"
  manifest_set "$manifest" "LANE" "$lane"
  manifest_set "$manifest" "LLAMA_REPO" "$PINNED_REPO"
  manifest_set "$manifest" "LLAMA_BRANCH" "$PINNED_BRANCH"
  manifest_set "$manifest" "LLAMA_REF" "$ref"
  manifest_set "$manifest" "SOURCE_CACHE" "$source_dir"
  manifest_set "$manifest" "SOURCE_BIN" "$source_bin"
  manifest_set "$manifest" "TOOLBOX" "$toolbox"
  manifest_set "$manifest" "PATCH_FILE" "$PATCH_FILE"
  manifest_set "$manifest" "PATCH_SHA256" "$patch_hash"
  manifest_set "$manifest" "LLAMA_SERVER_VERSION" "$version"
  manifest_set "$manifest" "BUILD_STARTED_AT" "$(timestamp)"
  manifest_set "$manifest" "BUILD_FINISHED_AT" "$(timestamp)"
  manifest_set "$manifest" "BUILD_STATUS" "imported"

  log "Imported current backend as: $build_id"
  if [[ "$promote" -eq 1 ]]; then
    cmd_promote --candidate "$build_id"
  else
    printf '%s\n' "$build_id"
  fi
}

http_json() {
  local method="$1"
  local url="$2"
  local payload="${3:-}"
  if [[ "$method" == "GET" ]]; then
    curl -fsS "$url"
  else
    curl -fsS -X "$method" "$url" -H 'Content-Type: application/json' -d "$payload"
  fi
}

wait_health() {
  local endpoint="$1"
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if curl -fsS "$endpoint/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_model_loaded() {
  local endpoint="$1"
  local model="$2"
  local deadline=$((SECONDS + 900))
  local status="unknown"
  while (( SECONDS < deadline )); do
    status="$(curl -fsS "$endpoint/models?autoload=false" 2>/dev/null | python3 -c '
import json
import sys
target = sys.argv[1]
payload = json.load(sys.stdin)
for item in payload.get("data", []):
    if item.get("id") == target or item.get("model") == target:
        print((item.get("status") or {}).get("value", "unknown"))
        raise SystemExit(0)
print("missing")
' "$model" || echo "unknown")"
    [[ "$status" == "loaded" ]] && return 0
    [[ "$status" == "missing" ]] && return 1
    sleep 2
  done
  echo "Timed out waiting for $model to load (last status: $status)" >&2
  return 1
}

load_model() {
  local endpoint="$1"
  local model="$2"
  local payload
  payload="$(python3 - "$model" <<'PY'
import json
import sys
print(json.dumps({"model": sys.argv[1]}))
PY
)"
  http_json POST "$endpoint/models/load" "$payload" >/dev/null
  wait_model_loaded "$endpoint" "$model"
}

chat_smoke() {
  local endpoint="$1"
  local model="$2"
  local prompt="$3"
  local payload
  payload="$(python3 - "$model" "$prompt" <<'PY'
import json
import sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": sys.argv[2]}],
    "temperature": 0,
    "max_tokens": 12,
}))
PY
)"
  http_json POST "$endpoint/v1/chat/completions" "$payload" >/dev/null
}

vision_smoke() {
  local endpoint="$1"
  local model="$2"
  local payload
  payload="$(python3 - "$model" <<'PY'
import json
import sys
red_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mP8z8BQDwAFgwJ/lQn4MwAAAABJRU5ErkJggg=="
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What is the dominant color? Answer with one word."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + red_png}},
        ],
    }],
    "temperature": 0,
    "max_tokens": 8,
}))
PY
)"
  http_json POST "$endpoint/v1/chat/completions" "$payload" >/dev/null
}

record_runtime_models() {
  local manifest="$1"
  local profile="${2:-dual}"
  [[ -f "$MODELS_INI" ]] || return 0
  python3 - "$MODELS_INI" "$manifest" "$profile" "$DUAL_MODELS" <<'PY'
import configparser
import hashlib
import pathlib
import sys

ini_path = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
profile = sys.argv[3]
models = sys.argv[4].split()

cfg = configparser.ConfigParser()
cfg.optionxform = str
cfg.read(ini_path)

def sha256(path: str) -> str:
    p = pathlib.Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"

lines = []
lines.append(f"RUNTIME_PROFILE={q(profile)}")
for i, model_id in enumerate(models, 1):
    if not cfg.has_section(model_id):
        continue
    section = cfg[model_id]
    model_path = section.get("model", "")
    mmproj_path = section.get("mmproj", "")
    prefix = f"RUNTIME_MODEL_{i}"
    lines.append(f"{prefix}_ID={q(model_id)}")
    lines.append(f"{prefix}_PATH={q(model_path)}")
    lines.append(f"{prefix}_SHA256={q(sha256(model_path))}")
    lines.append(f"{prefix}_MMPROJ_PATH={q(mmproj_path)}")
    lines.append(f"{prefix}_MMPROJ_SHA256={q(sha256(mmproj_path))}")
    for key in ("spec-type", "spec-draft-n-max", "spec-draft-n-min", "chat-template-file", "chat-template-kwargs"):
        if key in section:
            env_key = key.upper().replace("-", "_")
            lines.append(f"{prefix}_{env_key}={q(section.get(key, ''))}")

with manifest_path.open("a", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")
PY
}

append_perf_metrics_from_log() {
  local manifest="$1"
  local log_file="$2"
  [[ -f "$log_file" ]] || return 0
  python3 - "$manifest" "$log_file" <<'PY'
import pathlib
import re
import sys

manifest = pathlib.Path(sys.argv[1])
log_path = pathlib.Path(sys.argv[2])
text = log_path.read_text(errors="replace")

prompt = re.findall(r"prompt eval time =\s*([0-9.]+) ms /\s*([0-9]+) tokens .*?,\s*([0-9.]+) tokens per second\)", text)
decode = re.findall(r"\beval time =\s*([0-9.]+) ms /\s*([0-9]+) tokens .*?,\s*([0-9.]+) tokens per second\)", text)
total = re.findall(r"total time =\s*([0-9.]+) ms /\s*([0-9]+) tokens", text)

def q(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

lines = []
if prompt:
    ms, tokens, tok_s = prompt[-1]
    lines.extend([
        f"SMOKE_PROMPT_EVAL_MS={q(ms)}",
        f"SMOKE_PROMPT_TOKENS={q(tokens)}",
        f"SMOKE_PROMPT_TOK_S={q(tok_s)}",
    ])
if decode:
    ms, tokens, tok_s = decode[-1]
    lines.extend([
        f"SMOKE_DECODE_EVAL_MS={q(ms)}",
        f"SMOKE_DECODE_TOKENS={q(tokens)}",
        f"SMOKE_DECODE_TOK_S={q(tok_s)}",
    ])
if total:
    ms, tokens = total[-1]
    lines.extend([
        f"SMOKE_TOTAL_EVAL_MS={q(ms)}",
        f"SMOKE_TOTAL_TOKENS={q(tokens)}",
    ])

with manifest.open("a", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")
PY
}

candidate_is_current() {
  local candidate="$1"
  [[ -L "$STORE/current" ]] || return 1
  [[ "$(basename "$(readlink "$STORE/current")")" == "$candidate" ]]
}

perf_gate_against_current() {
  local candidate="$1"
  local manifest="$2"
  local baseline="$STORE/current/manifest.env"
  candidate_is_current "$candidate" && return 0
  [[ -f "$baseline" ]] || return 0

  local prompt_cur prompt_base decode_cur decode_base total_cur total_base
  prompt_cur="$(manifest_value "$manifest" SMOKE_PROMPT_TOK_S 2>/dev/null || true)"
  prompt_base="$(manifest_value "$baseline" SMOKE_PROMPT_TOK_S 2>/dev/null || true)"
  decode_cur="$(manifest_value "$manifest" SMOKE_DECODE_TOK_S 2>/dev/null || true)"
  decode_base="$(manifest_value "$baseline" SMOKE_DECODE_TOK_S 2>/dev/null || true)"
  total_cur="$(manifest_value "$manifest" SMOKE_TOTAL_EVAL_MS 2>/dev/null || true)"
  total_base="$(manifest_value "$baseline" SMOKE_TOTAL_EVAL_MS 2>/dev/null || true)"

  python3 - "$prompt_cur" "$prompt_base" "$decode_cur" "$decode_base" "$total_cur" "$total_base" <<'PY'
import sys

prompt_cur, prompt_base, decode_cur, decode_base, total_cur, total_base = sys.argv[1:]

def maybe_float(value):
    try:
        return float(value)
    except Exception:
        return None

checks = [
    ("prompt tok/s", maybe_float(prompt_cur), maybe_float(prompt_base), "higher"),
    ("decode tok/s", maybe_float(decode_cur), maybe_float(decode_base), "higher"),
    ("total ms", maybe_float(total_cur), maybe_float(total_base), "lower"),
]

for name, cur, base, direction in checks:
    if cur is None or base is None or base <= 0:
        continue
    if direction == "higher" and cur < base * 0.85:
        raise SystemExit(f"{name} regressed by >15%: current={cur}, baseline={base}")
    if direction == "lower" and cur > base * 1.15:
        raise SystemExit(f"{name} regressed by >15%: current={cur}, baseline={base}")
PY
}

cmd_smoke() {
  local candidate="" profile="dual" port="$DEFAULT_CANARY_PORT" toolbox="$DEFAULT_TOOLBOX"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --candidate) candidate="${2:-}"; shift 2 ;;
      --profile) profile="${2:-}"; shift 2 ;;
      --port) port="${2:-}"; shift 2 ;;
      --toolbox) toolbox="${2:-}"; shift 2 ;;
      *) die "Unknown smoke argument: $1" ;;
    esac
  done
  [[ -n "$candidate" ]] || die "smoke requires --candidate"
  [[ "$profile" == "dual" || "$profile" == "27b-text-mtp" ]] || die "Unknown smoke profile: $profile"

  init_store
  need_cmd curl
  need_cmd python3
  local tb_cmd
  tb_cmd="$(toolbox_cmd)" || die "Missing toolbox/distrobox command"

  local prefix="$BUILDS_DIR/$candidate"
  local manifest="$prefix/manifest.env"
  [[ -x "$prefix/bin/llama-server" ]] || die "Candidate llama-server is not executable: $prefix/bin/llama-server"

  local endpoint="http://127.0.0.1:$port"
  if curl -fsS "$endpoint/health" >/dev/null 2>&1; then
    die "Canary port is already serving health: $endpoint"
  fi

  local log_file="$LOG_DIR/smoke-${candidate}-${profile}-$(build_stamp).log"
  local ld_path models_max preload_models
  ld_path="$(backend_ld_library_path "$prefix")"
  models_max="$MODELS_MAX_DUAL"
  preload_models="$DUAL_MODELS"
  if [[ "$profile" == "27b-text-mtp" ]]; then
    models_max="$MODELS_MAX_TEXT"
    preload_models="$TEXT_MTP_MODEL"
  fi

  log "Starting canary server on $endpoint"
  local canary_cmd
  canary_cmd="$(printf 'export LD_LIBRARY_PATH=%q; exec %q ' "$ld_path:\${LD_LIBRARY_PATH:-}" "$prefix/bin/llama-server")"
  canary_cmd+="$(printf '%q ' \
    --no-mmap \
    -ngl 999 \
    -fa on \
    -c "$CTX" \
    -b "$BATCH" \
    --ubatch-size "$UBATCH_SIZE" \
    --threads "$(nproc)" \
    --threads-batch "$(nproc)" \
    --cache-type-k "$CACHE_K" \
    --cache-type-v "$CACHE_V" \
    --no-perf \
    --host 127.0.0.1 \
    --port "$port" \
    --models-dir "$HOME/models" \
    --models-max "$models_max" \
    --no-cache-prompt \
    --cache-reuse 256 \
    --slot-prompt-similarity 0.10 \
    --models-preset "$MODELS_INI" \
    --parallel 1 \
    --metrics)"
  nohup "$tb_cmd" run -c "$toolbox" bash -lc "$canary_cmd" >>"$log_file" 2>&1 &
  local canary_pid=$!

  cleanup_canary() {
    if [[ -n "${canary_pid:-}" ]]; then
      kill "$canary_pid" >/dev/null 2>&1 || true
      wait "$canary_pid" >/dev/null 2>&1 || true
    fi
    local pkill_pattern
    pkill_pattern="${prefix}/bin/llama-server.*--port[[:space:]]+$port"
    "$tb_cmd" run -c "$toolbox" bash -lc "pkill -f '$(printf '%q' "$pkill_pattern")' || true" >/dev/null 2>&1 || true
  }
  trap cleanup_canary EXIT

  wait_health "$endpoint" || die "Canary did not become healthy; log: $log_file"

  load_model "$endpoint" "$TEXT_MTP_MODEL"
  chat_smoke "$endpoint" "$TEXT_MTP_MODEL" "Reply with exactly: ok"

  if ! grep -Eiq 'MTP|draft head|spec' "$log_file"; then
    die "Canary log does not show MTP/spec activity for $TEXT_MTP_MODEL: $log_file"
  fi

  "$SCRIPT_DIR/smoke-test-logprobs-tools.py" \
    --endpoint "$endpoint" \
    --model "$TEXT_MTP_MODEL" \
    --timeout 180 >>"$log_file" 2>&1

  if [[ "$profile" == "dual" ]]; then
    load_model "$endpoint" "$VISION_MODEL"
    vision_smoke "$endpoint" "$VISION_MODEL"
  fi

  local start_ms end_ms wall_ms
  start_ms="$(date +%s%3N)"
  chat_smoke "$endpoint" "$TEXT_MTP_MODEL" "Write one short JSON object with keys status and value."
  end_ms="$(date +%s%3N)"
  wall_ms=$((end_ms - start_ms))

  manifest_set "$manifest" "SMOKE_STATUS" "success"
  manifest_set "$manifest" "SMOKE_PROFILE" "$profile"
  manifest_set "$manifest" "SMOKE_PORT" "$port"
  manifest_set "$manifest" "SMOKE_LOG" "$log_file"
  manifest_set "$manifest" "SMOKE_WALL_MS" "$wall_ms"
  manifest_set "$manifest" "SMOKE_FINISHED_AT" "$(timestamp)"
  append_perf_metrics_from_log "$manifest" "$log_file"
  perf_gate_against_current "$candidate" "$manifest"
  record_runtime_models "$manifest" "$profile"

  log "Smoke passed for $candidate ($profile)"
  cleanup_canary
  trap - EXIT
}

validated_build_prefix() {
  local candidate="$1"
  local prefix="$BUILDS_DIR/$candidate"
  [[ -d "$prefix" ]] || die "Build not found: $candidate"
  [[ -x "$prefix/bin/llama-server" ]] || die "Build has no executable bin/llama-server: $candidate"
  printf '%s\n' "$prefix"
}

write_promoted_manifest() {
  local candidate="$1"
  local runtime_manifest="$STORE/runtime-manifest.env"
  local build_manifest="$BUILDS_DIR/$candidate/manifest.env"
  : >"$runtime_manifest"
  manifest_set "$runtime_manifest" "PROMOTED_BUILD_ID" "$candidate"
  manifest_set "$runtime_manifest" "PROMOTED_AT" "$(timestamp)"
  manifest_set "$runtime_manifest" "BUILD_MANIFEST" "$build_manifest"
  manifest_set "$runtime_manifest" "CTX" "$CTX"
  manifest_set "$runtime_manifest" "MODELS_MAX_DUAL" "$MODELS_MAX_DUAL"
  manifest_set "$runtime_manifest" "PARALLEL" "1"
  manifest_set "$runtime_manifest" "CACHE_K" "$CACHE_K"
  manifest_set "$runtime_manifest" "CACHE_V" "$CACHE_V"
  manifest_set "$runtime_manifest" "KNOWN_BAD_FLAGS" "--spec-draft-n-max 16 for current 27B MTP; 35B A3B MTP is not daily default; external draft/tokenizer mismatches are known-risk; 27B + mmproj is known-bad"
  record_runtime_models "$runtime_manifest" "dual"
}

cmd_promote() {
  local candidate=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --candidate) candidate="${2:-}"; shift 2 ;;
      *) die "Unknown promote argument: $1" ;;
    esac
  done
  [[ -n "$candidate" ]] || die "promote requires --candidate"
  init_store
  validated_build_prefix "$candidate" >/dev/null

  exec 200>"$LOCK_FILE"
  flock 200

  local old_current=""
  if [[ -L "$STORE/current" ]]; then
    old_current="$(readlink "$STORE/current")"
  fi

  if [[ -n "$old_current" ]]; then
    ln -sfn "$old_current" "$STORE/previous.tmp.$$"
    mv -Tf "$STORE/previous.tmp.$$" "$STORE/previous"
  fi

  ln -sfn "builds/$candidate" "$STORE/current.tmp.$$"
  mv -Tf "$STORE/current.tmp.$$" "$STORE/current"
  write_promoted_manifest "$candidate"
  manifest_set "$BUILDS_DIR/$candidate/manifest.env" "PROMOTED_AT" "$(timestamp)"
  log "Promoted backend: $candidate"
}

cmd_rollback() {
  local target=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --to) target="${2:-}"; shift 2 ;;
      *) die "Unknown rollback argument: $1" ;;
    esac
  done
  init_store

  exec 200>"$LOCK_FILE"
  flock 200

  if [[ -z "$target" ]]; then
    [[ -L "$STORE/previous" ]] || die "No previous backend symlink exists"
    target="$(basename "$(readlink "$STORE/previous")")"
  fi
  validated_build_prefix "$target" >/dev/null

  local old_current=""
  if [[ -L "$STORE/current" ]]; then
    old_current="$(readlink "$STORE/current")"
  fi

  if [[ -n "$old_current" ]]; then
    ln -sfn "$old_current" "$STORE/previous.tmp.$$"
    mv -Tf "$STORE/previous.tmp.$$" "$STORE/previous"
  fi

  ln -sfn "builds/$target" "$STORE/current.tmp.$$"
  mv -Tf "$STORE/current.tmp.$$" "$STORE/current"
  write_promoted_manifest "$target"
  log "Rolled back/promoted backend: $target"
}

cmd_status() {
  init_store
  echo "Store: $STORE"
  if [[ -L "$STORE/current" ]]; then
    echo "Current: $(readlink "$STORE/current")"
    local manifest="$STORE/current/manifest.env"
    if [[ -f "$manifest" ]]; then
      echo "Manifest: $manifest"
      for key in BUILD_ID BUILD_METHOD LANE LLAMA_REF TOOLBOX BUILD_STATUS SMOKE_STATUS LLAMA_SERVER_VERSION; do
        if value="$(manifest_get "$manifest" "$key" 2>/dev/null)"; then
          echo "$key=$value"
        fi
      done
    fi
  else
    echo "Current: <none>"
  fi
  if [[ -L "$STORE/previous" ]]; then
    echo "Previous: $(readlink "$STORE/previous")"
  else
    echo "Previous: <none>"
  fi
}

cmd_list() {
  init_store
  printf '%-48s %-10s %-12s %-14s %s\n' "BUILD_ID" "LANE" "STATUS" "SMOKE" "VERSION"
  local manifest build_id lane status smoke version
  shopt -s nullglob
  for manifest in "$BUILDS_DIR"/*/manifest.env; do
    build_id="$(basename "$(dirname "$manifest")")"
    lane="$(manifest_get "$manifest" LANE 2>/dev/null || true)"
    status="$(manifest_get "$manifest" BUILD_STATUS 2>/dev/null || true)"
    smoke="$(manifest_get "$manifest" SMOKE_STATUS 2>/dev/null || true)"
    version="$(manifest_get "$manifest" LLAMA_SERVER_VERSION 2>/dev/null || true)"
    printf '%-48s %-10s %-12s %-14s %s\n' "$build_id" "$lane" "$status" "${smoke:-n/a}" "$version"
  done
}

main() {
  local cmd="${1:-}"
  if [[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]]; then
    usage
    exit 0
  fi
  shift || true

  case "$cmd" in
    status) cmd_status "$@" ;;
    list) cmd_list "$@" ;;
    build) cmd_build "$@" ;;
    import-current) cmd_import_current "$@" ;;
    smoke) cmd_smoke "$@" ;;
    promote) cmd_promote "$@" ;;
    rollback) cmd_rollback "$@" ;;
    __build-inside) cmd_build_inside "$@" ;;
    *) die "Unknown command: $cmd" ;;
  esac
}

main "$@"
