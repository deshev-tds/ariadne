#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

if [[ "${ARIADNE_PATCH_TRACE:-0}" == "1" ]]; then
  PS4='+ [${BASH_SOURCE##*/}:${LINENO}] '
  set -x
fi

LOG_DIR="${ARIADNE_PATCH_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ariadne-llama-patch/logs}"
mkdir -p "$LOG_DIR"
RUN_ID="$(date '+%Y%m%dT%H%M%S')"
RUN_LOG="$LOG_DIR/refresh-${RUN_ID}.log"
CURRENT_TOOLBOX="startup"

exec > >(tee -a "$RUN_LOG") 2>&1

on_error() {
  local exit_code=$?
  log "ERROR: refresh failed with exit code $exit_code"
  log "Current toolbox: $CURRENT_TOOLBOX"
  log "Last command: ${BASH_COMMAND}"
  log "Refresh log: $RUN_LOG"
  exit "$exit_code"
}
trap on_error ERR

log "Ariadne patched toolbox refresh started"
log "Refresh log: $RUN_LOG"

# Known llama.cpp toolboxes and their device/runtime options.
# Non-llama toolboxes, such as ComfyUI, are intentionally not listed here.
declare -A TOOLBOXES

TOOLBOXES["llama-vulkan-amdvlk"]="docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-amdvlk --device /dev/dri --group-add video --security-opt seccomp=unconfined"
TOOLBOXES["llama-vulkan-radv"]="docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv --device /dev/dri --group-add video --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-6.4.4"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm-6.4.4 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-6.4.4-rocwmma"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm-6.4.4-rocwmma --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.2"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm-7.2 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.2.1"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm-7.2.1 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.2.1-pr21344"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm-7.2.1-pr21344 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm7-nightlies"]="docker.io/kyuz0/amd-strix-halo-toolboxes:rocm7-nightlies --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"

usage() {
  local exit_code="${1:-0}"
  echo "Usage: $0 [all|toolbox-name1 toolbox-name2 ...]"
  echo "       $0"
  echo
  echo "With no arguments, refreshes all patched llama.cpp toolboxes."
  echo "Available patched llama.cpp toolboxes:"
  for name in $(printf '%s\n' "${!TOOLBOXES[@]}" | sort); do
    echo "  - $name"
  done
  echo
  echo "Environment:"
  echo "  ARIADNE_PATCH_NO_CACHE=1   Build patched images with podman --no-cache"
  echo "  ARIADNE_LLAMA_REPO=URL     Override llama.cpp repo"
  echo "  ARIADNE_LLAMA_BRANCH=NAME  Override llama.cpp branch"
  echo "  ARIADNE_LLAMA_REF=REF      Pin exact llama.cpp ref; omitted resolves branch head"
  echo "  ARIADNE_PATCH_LOG_DIR=DIR  Write refresh/build logs under DIR"
  echo "  ARIADNE_PATCH_TRACE=1      Enable bash xtrace"
  exit "$exit_code"
}

IS_UBUNTU=false
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" == "ubuntu" ]]; then
    IS_UBUNTU=true
  fi
fi

if [[ "$IS_UBUNTU" == true ]]; then
  TOOLBOX_CMD="distrobox"
else
  TOOLBOX_CMD="toolbox"
fi

DEPENDENCIES=("git" "podman" "$TOOLBOX_CMD")
for cmd in "${DEPENDENCIES[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    if [[ "$cmd" == "distrobox" && "$IS_UBUNTU" == true ]]; then
      echo "Error: 'distrobox' is not installed. Ubuntu users must use distrobox instead of toolbox." >&2
    else
      echo "Error: '$cmd' is not installed." >&2
    fi
    exit 1
  fi
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage 0
fi

if [[ "$#" -lt 1 ]]; then
  log "No toolbox arguments supplied; defaulting to all patched llama.cpp toolboxes"
  mapfile -t SELECTED_TOOLBOXES < <(printf '%s\n' "${!TOOLBOXES[@]}" | sort)
elif [[ "$1" == "all" ]]; then
  mapfile -t SELECTED_TOOLBOXES < <(printf '%s\n' "${!TOOLBOXES[@]}" | sort)
else
  SELECTED_TOOLBOXES=()
  for arg in "$@"; do
    if [[ -n "${TOOLBOXES[$arg]+set}" ]]; then
      SELECTED_TOOLBOXES+=("$arg")
    else
      echo "Error: Unknown toolbox '$arg'" >&2
      usage 1
    fi
  done
fi

log "Selected toolboxes: ${SELECTED_TOOLBOXES[*]}"

resolve_llama_ref() {
  local repo="$1"
  local branch="$2"
  local ref=""

  ref="$(git ls-remote --heads "$repo" "$branch" | awk 'NR == 1 {print $1}')"
  if [[ -z "$ref" ]]; then
    ref="$(git ls-remote "$repo" "$branch" | awk 'NR == 1 {print $1}')"
  fi

  if [[ -z "$ref" ]]; then
    return 1
  fi

  printf '%s\n' "$ref"
}

LLAMA_REPO="${ARIADNE_LLAMA_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_BRANCH="${ARIADNE_LLAMA_BRANCH:-master}"
LLAMA_REF="${ARIADNE_LLAMA_REF:-}"

if [[ -z "$LLAMA_REF" ]]; then
  log "Resolving llama.cpp upstream ref"
  log "  repo:   $LLAMA_REPO"
  log "  branch: $LLAMA_BRANCH"
  if ! LLAMA_REF="$(resolve_llama_ref "$LLAMA_REPO" "$LLAMA_BRANCH")"; then
    echo "Error: could not resolve llama.cpp ref for $LLAMA_REPO $LLAMA_BRANCH" >&2
    exit 2
  fi
  log "  ref:    $LLAMA_REF"
else
  log "Using explicit llama.cpp ref"
  log "  repo:   $LLAMA_REPO"
  log "  branch: $LLAMA_BRANCH"
  log "  ref:    $LLAMA_REF"
fi

build_args=()
if [[ "${ARIADNE_PATCH_NO_CACHE:-0}" == "1" ]]; then
  build_args+=(--no-cache)
fi
build_args+=(--repo "$LLAMA_REPO")
build_args+=(--branch "$LLAMA_BRANCH")
build_args+=(--ref "$LLAMA_REF")

REFRESHED_TOOLBOXES=()

for name in "${SELECTED_TOOLBOXES[@]}"; do
  CURRENT_TOOLBOX="$name"
  config="${TOOLBOXES[$name]}"
  image="$(awk '{print $1}' <<<"$config")"
  options="${config#* }"
  patched_image="localhost/ariadne-${name}:latest"

  log "Refreshing $name"
  log "  upstream image: $image"
  log "  patched image:  $patched_image"

  log "Step: pulling upstream image"
  podman pull "$image"

  log "Step: building patched image before replacing the existing toolbox"
  "$SCRIPT_DIR/build-patched-image.sh" \
    --base-image "$image" \
    --toolbox-name "$name" \
    --tag "$patched_image" \
    "${build_args[@]}"

  log "Step: patched image built successfully"
  if $TOOLBOX_CMD list | grep -q "$name"; then
    log "Step: removing existing toolbox: $name"
    $TOOLBOX_CMD rm -f "$name"
  else
    log "Step: no existing toolbox named $name"
  fi

  log "Step: creating patched toolbox: $name"
  $TOOLBOX_CMD create "$name" --image "$patched_image" -- $options

  repo="${image%:*}"
  log "Step: cleaning dangling images for $repo"
  while read -r id; do
    [[ -n "$id" ]] || continue
    podman image rm -f "$id" >/dev/null 2>&1 || true
  done < <(podman images --format '{{.ID}} {{.Repository}}:{{.Tag}}' \
           | awk -v r="$repo" '$2==r":<none>" {print $1}')

  log "$name refreshed with Ariadne-patched llama-server"
  REFRESHED_TOOLBOXES+=("$name")
  echo
done

CURRENT_TOOLBOX="done"
log "Refreshed ${#REFRESHED_TOOLBOXES[@]} toolbox(es): ${REFRESHED_TOOLBOXES[*]}"
log "Ariadne patched toolbox refresh completed"
