# Device router

Status: NOT BUILT.

No program in this repository implements a LAN job router or a peer shuttle. No seat is allowed to build either one from this file or from a drawing. `artifacts/reference/design/arch_multi_device_ready.png` and `artifacts/reference/design/arch_phases_0_to_3.png` are pictures of a future shape. They are not authorization.

Cloud SPOC routing in `BOTS.md` stays the bot track. This router would not replace it. The Trident assistant in `artifacts/reference/tracks/IRIS_ASSISTANT.md` stays on Iris, makes no network call, and does not use this router.

## Locked LAN

Same subnet. DHCP reserved.

| Name | Address | Worker | Checkout |
| --- | --- | --- | --- |
| Iris | 192.168.16.45 | `trident-iris` | `C:\Users\eb-wjt\Downloads\Jarvis\Trident` |
| Nvidia | 192.168.16.31 | `trident-nvidia` | `C:\Users\px-wjt\Downloads\Jarvis\Trident` |
| Gateway | 192.168.16.4 |  |  |

EB-W machine id on Iris: `84403f85-8162-436b-9567-dd9255e82a60`.

Iris is the primary cook, the microphone, and the speakers. Nvidia stays leave-alone unless a route already named in `BOTS.md` uses it (Ask, when the go names Nvidia, or a review SPOC routes). That existing Ask path is not this router.

## Three pools

- Iris CPU
- Iris Vulkan iGPU (Intel Iris Xe on this PC)
- Nvidia CUDA (GTX 1060 6GB)

A future job router would place a job on one of those pools. It is not built.

## Shape that is not built

- Transparent Iris mic and speakers. The person still hears and speaks on Iris.
- A heavy job, for example TTS, may run on Nvidia and return files to Iris.
- The router does not replace cloud SPOC routing.
- A local Grok-bot proof of concept is a separate track from the Trident assistant. It is not `assistant.py`, and it is not this router.

## Peer shuttle

Phase 0-1 only, and held until Wojciech gives GO:

- Text and small files.
- Identical peers.
- LAN-only.

The shuttle is not built. Phases drawn past Phase 0-1 are not specified here and are not a go.

## Standing facts

- The job router is not built.
- The peer shuttle is not built.
- This file has no build procedure, no ports, and no extra hosts.
- Implementing either one requires Wojciech's explicit GO, and then a docs change that rewrites this file from zero so it matches the tree.
- Until that GO, a review Fails any patch that adds the router or the shuttle.

## Reviewer checks

Pass this file only when every line below is still true:

- The status line says not built.
- The locked addresses are Iris 192.168.16.45, Nvidia 192.168.16.31, and gateway 192.168.16.4, same subnet, DHCP reserved.
- The pools are Iris CPU, Iris Vulkan iGPU, and Nvidia CUDA (GTX 1060 6GB).
- The router does not replace cloud SPOC routing.
- Iris mic and speakers stay on Iris in the future shape, and a heavy job such as TTS may run on Nvidia and return files. That path is not built.
- A local Grok-bot proof of concept is named as a separate track from the Trident assistant.
- Peer shuttle Phase 0-1 is text and small files, identical peers, LAN-only, and held until Wojciech GO.
- The file does not tell anyone how to build it.
