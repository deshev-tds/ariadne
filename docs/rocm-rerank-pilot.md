# ROCm Rerank Pilot on the Strix Halo Box

This note exists for one practical reason:

when this stack needs to be rebuilt from scratch later, the reranking path should not have to be rediscovered from first principles.

It records what we observed, what hypotheses we tested, what worked, what did not, and the exact commands used to get the current host-side ROCm-backed reranking path working on the box.

It is intentionally a field note, not a polished product claim.

## What We Noticed First

The immediate symptom was not "retrieval is always bad". It was narrower:

- `search_web` and `fetch_url` were not doing real reranking
- hybrid search was intentionally disabled in this fork/runtime because it was not behaving well enough to justify the complexity
- local corpus evidence retrieval and offsec evidence retrieval were using their own heuristic scoring layers
- the cross-encoder reranking pilot improved some obviously bad rankings, but CPU latency was ugly enough to make unconditional rollout questionable

The concrete replay cases that mattered most were:

- `vasodilator_natural`
- `pneumonia_imaging`
- `psoriasis_morphology`
- `sepsis_management`
- `stroke_anticoagulation`
- two offsec cases

The important failure pattern was:

- some hard misranks were fixed by `BAAI/bge-reranker-v2-m3`
- some already-good cases regressed
- CPU reranking was expensive enough to force gating back into the conversation

## What We Tested

### 1. Feature-flagged corpus evidence reranking

We added a separate corpus-evidence reranking path, independent of hybrid search.

That pilot was wired into:

- `local_corpus_retrieve_evidence`
- `offsec_retrieve_evidence`

The model used for the pilot was:

- `BAAI/bge-reranker-v2-m3`

### Why This Model Was Chosen

The reranker choice was not random.

Several candidate directions were considered, but the practical selection criteria were deliberately conservative:

- minimal integration change for the current OWUI fork
- compatibility with the existing `sentence-transformers.CrossEncoder` loading path
- no need for a separate external service just to run the pilot
- multilingual coverage or at least a better fit for mixed-language/local-first usage than English-only narrow choices
- licensing and deployment characteristics that would not immediately create friction
- enough community visibility that the choice was not based on an obscure one-off model card

`BAAI/bge-reranker-v2-m3` was chosen as the first pilot for those reasons:

- it loads through the existing `CrossEncoder` path instead of demanding a custom serving stack
- it is positioned as a multilingual reranker, which is a better fit for this fork than a strictly English-only assumption
- it is lightweight enough to test locally without redesigning the retrieval layer around it
- in early saved-candidate replay, it fixed several of the exact misranks that mattered here

The useful contrast was not "best reranker on a leaderboard" versus "worst reranker".

It was:

- can we try a real cross-encoder reranker inside this stack
- without forcing a large architecture change
- and does that pilot visibly fix the concrete bad cases we already have

For this fork, that was the right first filter.

Other options were considered in the background, but they were less attractive as a first pilot for one or more of these reasons:

- more awkward integration
- more deployment friction
- less confidence around multilingual/local-first fit
- licensing or `trust_remote_code` concerns
- unclear value relative to the effort needed to wire them in

So the decision was:

- not "this is definitely the globally best reranker"
- but "this is the first serious reranker that fits the current system with the least architectural violence"

### 2. Live replay against the deployed OWUI instance

We did not treat this as a vibes-only change.

We added a live eval harness:

- `scripts/evals/rerank_pilot_live.py`

That harness replays a fixed set of synthetic but realistic retrieval cases through the live OWUI instance and saves result artifacts under:

- `agentic_artifacts/rerank_eval/`

The fixed replay case set used in this work was:

- `vasodilator_natural`
- `vasodilator_contraindications`
- `vasodilator_metabolism`
- `pneumonia_imaging`
- `psoriasis_morphology`
- `sepsis_management`
- `stroke_anticoagulation`
- `offsec_macos_malware`
- `offsec_recon_mapping`

The point of this harness was narrow and explicit:

- hold the synthetic prompts constant
- hit the real deployed OWUI instance
- observe actual tool outputs
- save artifacts locally for later comparison instead of trusting memory

## Exact Test Setup

The replay harness was run against the live OWUI instance with the corpus-evidence reranking toggle exposed in the admin UI.

The meaningful runtime states we compared were:

1. `baseline`
   Corpus evidence reranking off.
2. `always_cpu`
   Corpus evidence reranking on, before host-side ROCm acceleration was working.
3. `gated_cpu`
   Corpus evidence reranking on, with the minimal heuristic top-score gate.
4. `rocm_warm`
   Corpus evidence reranking on, host-side ROCm working, measured after the model and kernels were already warm.
5. `rocm_aot_warm2`
   Same as `rocm_warm`, but with `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`, measured after a second warm replay rather than the first post-restart pass.

The exact replay command shape was:

```bash
cd ~/open-webui
python3 scripts/evals/rerank_pilot_live.py \
  --api-key "$OWUI_API_KEY" \
  --output agentic_artifacts/rerank_eval/live-after-manual-run.json
```

Important practical note:

the first replay immediately after backend restart should be treated as a semi-cold measurement, not a steady-state one. That mattered for both plain ROCm and AOTriton.

### 3. Score-only gating

We tested a deliberately minimal gate:

- rerank only when heuristic `top1` score is below a threshold

This was added to avoid domain-specific "if section contains INDEX/References/..." logic.

That choice was intentional. The goal was to avoid turning retrieval control into an expanding pile of content-specific exceptions.

### 4. Host-side ROCm acceleration

The biggest practical question became:

is the reranker slow because the model choice is bad, or because it is running on CPU?

The answer was: on the box, it was running on CPU.

The backend virtualenv originally had a CUDA wheel that did not actually see any accelerator:

- `torch 2.10.0+cu128`
- `torch.cuda.is_available() == False`
- `torch.version.hip == None`

That meant the current OWUI process could not use the Strix Halo iGPU for reranking.

### 5. AOTriton experimental attention

After ROCm acceleration worked, we also tested:

- `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`

That test needed to be measured in warm state, not only immediately after restart.

## What Worked

### `BAAI/bge-reranker-v2-m3` did improve real retrieval failures

The reranker consistently improved several of the painful cases:

- `vasodilator_natural`
- `pneumonia_imaging`
- `psoriasis_morphology`

Offsec also became usable once the corpus path itself was fixed on disk.

### Host-side ROCm acceleration worked

The correct host-side shape was:

- keep OWUI on the host
- do not move OWUI into the llama.cpp toolbox
- install ROCm runtime packages on the host
- install ROCm PyTorch wheels into `backend/.venv`
- make OWUI auto-detect the accelerator

Once this was done, the backend saw:

- `DEVICE_TYPE cuda`
- `torch.cuda.is_available() == True`
- `torch.version.hip != None`
- device name `Radeon 8060S Graphics`

This was the turning point.

### The auto-detect patch worked

Two local changes made the host ROCm path actually usable by OWUI:

- `backend/open_webui/env.py` now chooses `DEVICE_TYPE="cuda"` based on real `torch.cuda.is_available()` instead of only `USE_CUDA_DOCKER`
- `pull_and_run.sh` now sources `/etc/profile.d/rocm-owui.sh` before backend start

That means backend restart now keeps the ROCm runtime available without relying on a manually prepared interactive shell.

## What Did Not Work

### CPU reranking as a default-on story

CPU reranking improved some cases, but it made the overall tradeoff too ugly.

The issue was not just "a little slower". It was enough slower to keep gating in the critical path discussion.

### AOTriton as a slam-dunk optimization

The first AOTriton run looked worse than plain ROCm.

A second warm run showed that the first run was semi-cold and overstated the cost.

Final practical conclusion:

- `plain ROCm` is the big win
- `AOTriton=1` is not a dramatic additional win
- after warm-up it is roughly on par, maybe slightly better, but not transformational

So we left `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` enabled for now, but it should be treated as optional tuning, not the main story.

### Score-only gating as a complete answer

The minimal gate helped avoid at least one bad regression, but it did not eliminate all regression cases, and it did not by itself make the latency problem go away before ROCm acceleration was in place.

Its remaining value is now mostly:

- regression control

not:

- emergency latency control

## Exact Results We Actually Used

These are the numbers that drove the decisions.

### Aggregate Timing

`baseline`

- all cases: mean `3.44s`, median `2.78s`, max `6.52s`
- local-only: mean `3.19s`, median `2.62s`, max `6.52s`

`always_cpu`

- all cases: mean `18.45s`, median `21.6s`, max `25.49s`
- local-only: mean `16.67s`, median `14.62s`, max `23.62s`

`gated_cpu`

- all cases: mean `14.51s`, median `11.63s`, max `25.4s`
- local-only: mean `17.34s`, median `15.49s`, max `25.4s`

`rocm_warm`

- all cases: mean `10.82s`, median `9.37s`, max `19.77s`
- local-only: mean `13.07s`, median `12.74s`, max `19.77s`

`rocm_aot_warm2`

- all cases: mean `10.6s`, median `9.37s`, max `19.25s`
- local-only: mean `12.8s`, median `12.74s`, max `19.25s`

### Case-Level Results That Mattered

These were the concrete before/after examples that drove decisions more than any abstract metric did.

`vasodilator_natural`

- baseline top1: `C A S E S T U D Y > C A S E   S T U D Y`
- reranked top1: the relevant vasodilator treatment section
- this was a real fix, not a cosmetic shuffle

`pneumonia_imaging`

- baseline top1: `Radiologic Features to Differentiate Endemic Fungi on Imaging`
- reranked top1: `Bronchopneumonia (Lobular Pneumonia)`
- this was one of the clearest retrieval wins

`psoriasis_morphology`

- baseline top1 stayed under the same generic section label `Clinical Features`
- but reranking moved the actually relevant psoriasis passage to the top
- positive signal count improved from `2` to `5`, and negative hits dropped from `2` to `0`

`sepsis_management`

- baseline top1: `Management`
- `always_cpu` regressed to `References > Management`
- `gated_cpu` and later ROCm runs avoided that regression by skipping rerank there

`stroke_anticoagulation`

- baseline top1: `Anticoagulation in Acute Ischaemic Stroke > Anticoagulation in Acute Ischaemic Stroke`
- reranked top1: `Venous Thromboembolism`
- this remained a persistent regression case across CPU and ROCm runs

`offsec_macos_malware`

- once the offsec corpus path itself was fixed on disk, the case became usable
- `always_cpu` top1: `Checking Binary Origins`
- `rocm_warm` top1: `The Importance of Code Signing in Malware Detection`
- both were usable, but the important story here was that the path became fast and operational again

`offsec_recon_mapping`

- `always_cpu` top1: `Vulnerability assessment`
- `rocm_warm` top1: `Active Reconnaissance`
- positive signal stayed nonzero, and the path became much faster because reranking was skipped there

### What Those Numbers Meant In Practice

The decisions were based on the following practical readings:

- `always_cpu` proved the model could improve real misranks, but the latency cost was ugly
- `gated_cpu` proved a minimal gate could save at least one good case (`sepsis_management`), but it was not enough to solve everything
- `rocm_warm` changed the conversation from "reranking is too expensive" to "reranking is viable, now focus on residual regression control"
- `rocm_aot_warm2` showed that the experimental AOTriton flag was not catastrophic in warm state, but also not a massive win

That is why the final stance ended up being:

- keep the reranking lane
- keep ROCm acceleration
- keep gating as a quality-control lever, not as a desperation latency hack
- do not overclaim the value of the AOTriton flag

## Current Practical Verdict

At this point the useful conclusion is:

- yes, a corpus-evidence reranking lane is worth keeping
- yes, `BAAI/bge-reranker-v2-m3` is a reasonable pilot model for this stack
- yes, host-side ROCm acceleration materially improves the viability of that choice
- maybe keep or refine gating, but now for ranking-risk control rather than panic over CPU latency
- do not rely on hybrid search being enabled just to get evidence reranking

## Rebuild Runbook

These are the exact commands that got the current host-side ROCm reranking setup working.

### 1. Install host ROCm package repo

```bash
sudo tee /etc/yum.repos.d/rocm.repo >/dev/null <<'EOF'
[ROCm-7.2]
name=ROCm 7.2
baseurl=https://repo.radeon.com/rocm/rhel10/7.2/main
enabled=1
priority=50
gpgcheck=1
gpgkey=https://repo.radeon.com/rocm/rocm.gpg.key
EOF
```

### 2. Install host ROCm runtime packages

These were installed incrementally while resolving missing shared libraries. This consolidated list is the practical result:

```bash
sudo dnf install -y \
  hip-runtime-amd \
  hipblas \
  rocblas \
  roctracer \
  rocminfo \
  miopen-hip \
  hipfft \
  hiprand \
  hipsolver \
  hipsparse \
  hipsparselt \
  rccl
```

### 3. Make ROCm libraries persistent across reboot

```bash
sudo tee /etc/ld.so.conf.d/rocm-owui.conf >/dev/null <<'EOF'
/opt/rocm/lib
/opt/rocm/lib64
/opt/rocm/llvm/lib
EOF

sudo tee /etc/profile.d/rocm-owui.sh >/dev/null <<'EOF'
export ROCM_PATH=/opt/rocm
export HIP_PATH=/opt/rocm
export PATH=/opt/rocm/bin:/opt/rocm/llvm/bin:$PATH
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
EOF

sudo ldconfig
source /etc/profile.d/rocm-owui.sh
```

### 4. Install ROCm PyTorch wheels into `backend/.venv`

```bash
cd ~/open-webui
source backend/.venv/bin/activate

python -m pip install -U pip wheel

mkdir -p ~/tmp/owui-rocm-wheels
cd ~/tmp/owui-rocm-wheels

wget https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.1/torch-2.9.1%2Brocm7.2.1.lw.gitff65f5bc-cp312-cp312-linux_x86_64.whl
wget https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.1/torchvision-0.24.0%2Brocm7.2.1.gitb919bd0c-cp312-cp312-linux_x86_64.whl
wget https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.1/torchaudio-2.9.0%2Brocm7.2.1.gite3c6ee2b-cp312-cp312-linux_x86_64.whl
wget https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.1/triton-3.5.1%2Brocm7.2.1.gita272dfa8-cp312-cp312-linux_x86_64.whl

python -m pip uninstall -y torch torchvision torchaudio triton

python -m pip install \
  ./torch-2.9.1+rocm7.2.1.lw.gitff65f5bc-cp312-cp312-linux_x86_64.whl \
  ./torchvision-0.24.0+rocm7.2.1.gitb919bd0c-cp312-cp312-linux_x86_64.whl \
  ./torchaudio-2.9.0+rocm7.2.1.gite3c6ee2b-cp312-cp312-linux_x86_64.whl \
  ./triton-3.5.1+rocm7.2.1.gita272dfa8-cp312-cp312-linux_x86_64.whl
```

### 5. Verify the backend virtualenv actually sees ROCm

Run this from `backend/`, not the repo root:

```bash
cd ~/open-webui/backend
source /etc/profile.d/rocm-owui.sh
source .venv/bin/activate

python - <<'PY'
from open_webui.env import DEVICE_TYPE
import os
import torch

print("DEVICE_TYPE", DEVICE_TYPE)
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
print("torch.version.cuda", torch.version.cuda)
print("torch.version.hip", torch.version.hip)
print("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", os.environ.get("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL"))
if torch.cuda.is_available():
    print("device0", torch.cuda.get_device_name(0))
PY
```

Expected shape:

- `DEVICE_TYPE cuda`
- `cuda_available True`
- `torch.version.hip` populated

### 6. Restart OWUI with the backend env sourced automatically

```bash
cd ~/open-webui
bash ./pull_and_run.sh restart
```

The local `pull_and_run.sh` in this repo now sources `/etc/profile.d/rocm-owui.sh` before backend start.

## Live Eval Harness

The replay harness can be run like this:

```bash
cd ~/open-webui
python3 scripts/evals/rerank_pilot_live.py \
  --api-key "$OWUI_API_KEY" \
  --output agentic_artifacts/rerank_eval/live-after-manual-run.json
```

Useful comparison artifacts from this work:

- `agentic_artifacts/rerank_eval/live-baseline-2026-04-02.json`
- `agentic_artifacts/rerank_eval/live-after-offsec-fixed-2026-04-02.json`
- `agentic_artifacts/rerank_eval/live-after-gated-2026-04-02.json`
- `agentic_artifacts/rerank_eval/live-after-rocm-warm-2026-04-03.json`
- `agentic_artifacts/rerank_eval/live-after-rocm-aotriton-warm2-2026-04-03.json`

## If This Breaks Again

The fastest diagnostic loop is:

1. check `rocminfo`
2. check `torch.cuda.is_available()` and `torch.version.hip`
3. check `from open_webui.env import DEVICE_TYPE`
4. run the live rerank harness again instead of trusting a single console impression

If `torch` import fails with a missing shared library, inspect the installed torch libs and resolve missing packages systematically:

```bash
cd ~/open-webui
source /etc/profile.d/rocm-owui.sh
source backend/.venv/bin/activate

TORCH_LIB_DIR="$HOME/open-webui/backend/.venv/lib/python3.12/site-packages/torch/lib"

find "$TORCH_LIB_DIR" -maxdepth 1 -type f -name '*.so*' -print0 \
| xargs -0 -n1 ldd 2>/dev/null \
| awk '/not found/ {print $1}' \
| sort -u
```

That command is how we stopped guessing and found the missing ROCm libraries directly.
