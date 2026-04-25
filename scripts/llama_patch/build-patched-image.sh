#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" >&2
}

if [[ "${ARIADNE_PATCH_TRACE:-0}" == "1" ]]; then
  PS4='+ [${BASH_SOURCE##*/}:${LINENO}] '
  set -x
fi

BASE_IMAGE=""
BACKEND=""
TOOLBOX_NAME=""
TAG=""
LLAMA_REPO="https://github.com/ggml-org/llama.cpp.git"
LLAMA_BRANCH="master"
LLAMA_REF=""
NO_CACHE=0

usage() {
  cat <<'USAGE'
Usage:
  build-patched-image.sh --base-image IMAGE [options]

Builds a derivative toolbox image from a kyuz0 llama.cpp toolbox image.
The derivative image recompiles llama-server with the Ariadne patch applied.
No packages are installed on the host OS; build dependencies are installed
inside the image layer only.

Required:
  --base-image IMAGE       Base kyuz0 image, e.g. docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv

Options:
  --toolbox-name NAME      Used for backend/tag auto-detection
  --backend BACKEND        vulkan-radv, vulkan-amdvlk, rocm-6.4.4, rocm-7.2.1, or rocm7-nightlies
  --tag IMAGE_TAG          Output tag. Default: localhost/ariadne-llama-<backend>:latest
  --repo URL               llama.cpp git repository
  --branch BRANCH          llama.cpp branch. Default: master
  --ref SHA_OR_REF         Optional exact upstream ref to checkout after clone
  --no-cache               Pass --no-cache to podman build
  -h, --help               Show this help

Environment:
  ARIADNE_PATCH_LOG_DIR=DIR  Write build logs under DIR
  ARIADNE_PATCH_TRACE=1      Enable bash xtrace

Examples:
  ./build-patched-image.sh \
    --base-image docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv \
    --toolbox-name llama-vulkan-radv \
    --tag localhost/ariadne-llama-vulkan-radv:latest
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-image) BASE_IMAGE="${2:-}"; shift 2 ;;
    --toolbox-name) TOOLBOX_NAME="${2:-}"; shift 2 ;;
    --backend) BACKEND="${2:-}"; shift 2 ;;
    --tag) TAG="${2:-}"; shift 2 ;;
    --repo) LLAMA_REPO="${2:-}"; shift 2 ;;
    --branch) LLAMA_BRANCH="${2:-}"; shift 2 ;;
    --ref) LLAMA_REF="${2:-}"; shift 2 ;;
    --no-cache) NO_CACHE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$BASE_IMAGE" ]]; then
  echo "Missing required --base-image" >&2
  usage >&2
  exit 2
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "podman is required" >&2
  exit 2
fi

detect_backend() {
  local text="$1"
  case "$text" in
    *vulkan-radv*) echo "vulkan-radv" ;;
    *vulkan-amdvlk*) echo "vulkan-amdvlk" ;;
    *rocm7-nightlies*) echo "rocm7-nightlies" ;;
    *rocm-6.4.4-rocwmma*) echo "rocm-6.4.4" ;;
    *rocm-6.4.4*) echo "rocm-6.4.4" ;;
    *rocm-7.2.1-pr21344*) echo "rocm-7.2.1-pr21344" ;;
    *rocm-7.2.1*) echo "rocm-7.2.1" ;;
    *rocm-7.2*) echo "rocm-7.2.1" ;;
    *rocm*) echo "rocm" ;;
    *) return 1 ;;
  esac
}

if [[ -z "$BACKEND" ]]; then
  if ! BACKEND="$(detect_backend "${TOOLBOX_NAME} ${BASE_IMAGE}")"; then
    echo "Could not auto-detect backend from toolbox/image; pass --backend" >&2
    exit 2
  fi
fi

case "$BACKEND" in
  vulkan-radv|vulkan-amdvlk|rocm|rocm-6*|rocm-7*|rocm7-nightlies) ;;
  *) echo "Unsupported backend: $BACKEND" >&2; exit 2 ;;
esac

if [[ -z "$TAG" ]]; then
  safe_backend="$(printf '%s' "$BACKEND" | tr -c '[:alnum:]._-' '-')"
  TAG="localhost/ariadne-llama-${safe_backend}:latest"
fi

LOG_DIR="${ARIADNE_PATCH_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ariadne-llama-patch/logs}"
mkdir -p "$LOG_DIR"
safe_tag="$(printf '%s' "$TAG" | sed 's#[/:@]#_#g; s#[^A-Za-z0-9._-]#-#g')"
BUILD_LOG="$LOG_DIR/build-${safe_tag}-$(date '+%Y%m%dT%H%M%S').log"

on_error() {
  local exit_code=$?
  log "ERROR: build-patched-image.sh failed with exit code $exit_code"
  log "Last command: ${BASH_COMMAND}"
  log "Build log: $BUILD_LOG"
  exit "$exit_code"
}
trap on_error ERR

build_args=(
  build
  --file "$SCRIPT_DIR/Containerfile.patched-llama"
  --build-arg "BASE_IMAGE=$BASE_IMAGE"
  --build-arg "BACKEND=$BACKEND"
  --build-arg "LLAMA_REPO=$LLAMA_REPO"
  --build-arg "LLAMA_BRANCH=$LLAMA_BRANCH"
  --build-arg "LLAMA_REF=$LLAMA_REF"
  --tag "$TAG"
)

if [[ "$NO_CACHE" -eq 1 ]]; then
  build_args+=(--no-cache)
fi

build_args+=("$SCRIPT_DIR")

log "Building patched llama.cpp image"
log "  base:      $BASE_IMAGE"
log "  backend:   $BACKEND"
log "  output:    $TAG"
log "  repo:      $LLAMA_REPO"
log "  branch:    $LLAMA_BRANCH"
log "  build log: $BUILD_LOG"
if [[ -n "$LLAMA_REF" ]]; then
  log "  ref:       $LLAMA_REF"
fi

set +e
podman "${build_args[@]}" 2>&1 | tee "$BUILD_LOG"
build_status="${PIPESTATUS[0]}"
set -e

if [[ "$build_status" -ne 0 ]]; then
  log "podman build failed with exit code $build_status"
  log "Build log: $BUILD_LOG"
  exit "$build_status"
fi

log "Built patched image: $TAG"
printf '%s\n' "$TAG"
