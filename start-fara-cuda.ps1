# Fara 1.5 4B on this GTX 1060, llama.cpp b11756 (d80619397), CUDA 12.4.
# CUDA 13.4 builds of this commit do not include sm_61. This one does (61-virtual).
# Weights and the vision tower stay on CUDA0. Nothing is fit-shrunk onto the CPU.
# Leave this window open. The laptop talks to http://192.168.16.31:1234
# The first look after a cold NVIDIA kernel cache compiles Pascal PTX and is slow.
# Later looks are the fast ones. Do not use the Vulkan folder's llama-server for this.
# Tuned 2026-10-22 (eye-bench): -c 28672 (8 imgs x 1125 tok + 60-step text history + system), --image-min-tokens 256 (= main.py FARA_MIN_TOKENS), -n 512.
$d = 'C:\Users\px-wjt\AppData\Local\Programs\llama.cpp-cuda-12.4'
$m = 'C:\Users\px-wjt\AppData\Local\Programs\llama.cpp\models'
Set-Location $d
& "$d\llama-server.exe" `
  -m "$m\Fara1.5-4B-Q4_K_M.gguf" `
  --mmproj "$m\mmproj-Fara1.5-4B-Q8_0.gguf" `
  --host 0.0.0.0 --port 1234 `
  --alias fara-1.5-4b,retina `
  --device CUDA0 --split-mode none -ngl all --fit off `
  --override-tensor 'token_embd.weight=CUDA0' `
  --mmproj-offload --mmproj-device CUDA0 `
  --kv-offload --op-offload `
  -c 28672 -np 1 -fa on -ctk q8_0 -ctv q8_0 `
  -t 8 -b 2048 -ub 1024 `
  --image-min-tokens 256 --image-max-tokens 4096 `
  --mtmd-batch-max-tokens 4096 `
  --reasoning off -n 512
