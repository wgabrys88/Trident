# Device router

Status: NOT BUILT.

No program in this repository implements a LAN job router, a peer shuttle, or a `local_bots` package. No seat may build them from this file or from a drawing. `artifacts/reference/design/arch_multi_device_ready.png` and `artifacts/reference/design/arch_phases_0_to_3.png` are pictures of a future shape. They are not authorization.

Cloud SPOC routing in `BOTS.md` stays the bot track. This router would not replace it. `assistant.py` stays on Iris for the near term, makes no network call, and does not use this router. See `artifacts/reference/tracks/IRIS_ASSISTANT.md`.

## Locked LAN

Same subnet. DHCP reserved.

| Name | Address | Worker | Checkout |
| --- | --- | --- | --- |
| Iris | 192.168.16.45 | `trident-iris` | `C:\Users\eb-wjt\Downloads\Jarvis\Trident` |
| Nvidia | 192.168.16.31 | `trident-nvidia` | `C:\Users\px-wjt\Downloads\Jarvis\Trident` |
| Gateway | 192.168.16.4 |  |  |

EB-W machine id on Iris: `84403f85-8162-436b-9567-dd9255e82a60`.

Iris is the primary cook, the microphone, and the speakers. Nvidia stays leave-alone unless a route already named in `BOTS.md` uses it (Ask, when the go names Nvidia, or a review SPOC routes). That existing Ask path is not this router.

## Pools

Three pool ids. The machine behind each id:

| Pool id | Machine |
| --- | --- |
| `iris_cpu` | Iris CPU |
| `iris_vulkan` | Iris Vulkan iGPU (Intel Iris Xe) |
| `nvidia_cuda` | Nvidia CUDA (GTX 1060 6GB) |

`target_pool` is one of `iris_cpu`, `iris_vulkan`, or `nvidia_cuda`. Nothing in the tree routes a job to those ids. The router is not built.

## Future surface

Not built. These names are the contract for a later GO. This file does not say how to code them.

Peer job envelope fields:

- `job_id`
- `job_type`
- `target_pool`
- `module`
- `inputs`
- `outputs`
- `priority`
- `timeout_s`
- `compat`

API surface:

- `submit`
- `status`
- `fetch`
- `cancel`

Transparent TTS offload: a heavy TTS job may run off Iris and the result that comes back is a wav. Iris plays that wav on Speakers (`Speakers (Realtek(R) Audio)`). The microphone and the speakers stay on Iris.

`local_bots` is a separate package, not built. It is not `assistant.py`. It may share transport only with the assistant track. It does not share the assistant prompt path, and it does not put Grok on the assistant path.

## Peer shuttle

Phase 0-1 only, and held until Wojciech gives GO:

- Text and small files.
- Identical peers.
- LAN-only.

The shuttle is not built. Phases drawn past Phase 0-1 are not specified here and are not a go.

## Standing facts

- The job router is not built.
- The peer shuttle is not built.
- The `local_bots` package is not built.
- This file has no build procedure, no ports, and no extra hosts.
- Implementing any of it requires Wojciech's explicit GO, and then a docs change that rewrites this file from zero so it matches the tree.
- Until that GO, a review Fails any patch that adds the router, the shuttle, or `local_bots`.

## Reviewer checks

Pass this file only when every line below is still true:

- The status line says not built.
- The locked addresses are Iris 192.168.16.45, Nvidia 192.168.16.31, and gateway 192.168.16.4, same subnet, DHCP reserved.
- The pool ids are `iris_cpu`, `iris_vulkan`, and `nvidia_cuda`.
- The envelope fields are `job_id`, `job_type`, `target_pool`, `module`, `inputs`, `outputs`, `priority`, `timeout_s`, and `compat`.
- The API names are `submit`, `status`, `fetch`, and `cancel`.
- Transparent TTS offload returns a wav to Iris Speakers. Mic and speakers stay on Iris. That path is not built.
- `local_bots` is a separate package and may share transport only with the assistant track. It is not built.
- The router does not replace cloud SPOC routing.
- Peer shuttle Phase 0-1 is text and small files, identical peers, LAN-only, and held until Wojciech GO.
- The file does not tell anyone how to build it.
