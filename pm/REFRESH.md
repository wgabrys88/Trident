# Refresh the PM bootstrap

You are the existing PM. Use this when law changes. Do not add another rule file.

## When

The owner asks you to refresh the bootstrap, or you notice a durable rule has changed.

## Spawn

Spawn one Cursor agent. Prefer `trident-nvidia` when the change is docs only. Same defaults as `pm/BOOTSTRAP.md`: Grok 4.7, 256k context, reasoning xhigh, fast false. Never fast mode. Never a weaker model. Web search on. Cursor search only that seat's workspace and its subfolders, never the whole PC.

Paste the prompt below to that one agent. Do not spawn a second agent for the same refresh.

## After

When the agent reports, tell the owner one short line naming what updated.

## Agent prompt

You update Trident PM law on `runner-h`. Docs only. Do not start Trident.

Edit `pm/BOOTSTRAP.md` so it matches the durable rule change you were given. Leave every other durable rule in that file in place. Edit `pm/REFRESH.md` only if this refresh procedure itself changed. No other files. No product or runtime code. No new law docs.

Search only this seat workspace and its subfolders. Never the whole PC. Do not put secrets, tokens, real tdata, or private links in the files or in your report.

Discover the `runner-h` tip live. Commit this doc change and push it to `origin` on `runner-h`. No force-push. Do not write a commit SHA, a PR number, or a pid into these files as a durable fact.

The original scene gate is already complete. Do not use this edit to reopen it as unfinished work.

In your report, quote what changed.

## Never

Secrets, tokens, real tdata, or private links. Fast mode, a weaker model, or search outside the seat workspace. Any law file besides `pm/BOOTSTRAP.md` and `pm/REFRESH.md`. Using the bootstrap to reopen the completed scene as unfinished work.
