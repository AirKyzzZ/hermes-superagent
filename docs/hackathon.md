# Hermes Agent Plugin Hackathon: what we know

Announcement: https://x.com/NousResearch/status/2118094256438936062 (not readable without an X session, text still to confirm).

No official rules page found. The Hermes site, docs, marketplace page and source repo don't mention it. These facts come from one secondary report:
https://coinfomania.com/nousresearch-launches-hermes-agent-plugin-hackathon-offering/

| Item | Reported | Status |
| --- | --- | --- |
| Deadline | "open until November 11, 2026" | no timezone given, plan for the morning of Nov 11 UTC |
| Prizes | $20,000 total | split not stated |
| Judging | usefulness, reliability, creativity | secondary source |
| Submission | a pull request and a demo video | PR target not stated |
| Reward | accepted entries listed in the Hermes Marketplace | secondary source |
| Eligibility, teams | not stated | to confirm |

Likely PR target, not confirmed: `plugin-catalog/<name>.yaml` on `NousResearch/hermes-agent`, the only discovery route for out-of-tree plugins. See `website/docs/developer-guide/plugins/catalog-submission.md` in that repo.

## Catalog entry draft

For the owner to submit, once the pin is final. `sha` and the `image` URL must point at the same commit.

```yaml
name: hermes-superagent
repo: https://github.com/AirKyzzZ/hermes-superagent
sha: <40-char commit>
requires_hermes: ">=0.22.7"
version: "0.1.0"
description: "A resident router that dispatches and never executes, isolated workers that report only to it, team-written trigger rules, and an external watchdog that checks real progress and goal drift. Disclosure: spawns worker processes from the argv you configure, runs the outbound send command you configure, and stops or restarts its own workers. Inert until a profile sets a role."
platforms: [linux, macos]
maintainer: AirKyzzZ
tier: community
category: automation
docs_url: https://github.com/AirKyzzZ/hermes-superagent#readme
image: https://raw.githubusercontent.com/AirKyzzZ/hermes-superagent/<40-char commit>/assets/cover.png
capabilities:
  provides_tools: [superagent_dispatch, superagent_forward, superagent_status, superagent_report, superagent_tick]
  provides_hooks: [pre_tool_call, post_tool_call, pre_gateway_dispatch, pre_llm_call]
```
