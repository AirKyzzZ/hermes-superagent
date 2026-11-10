# Submission checklist

Hermes Agent Plugin Hackathon. The announcement says Wednesday 2026-11-11 at 23:00 Paris (22:00 UTC), but the form says it ends at 1pm PST, 22:00 Paris, so treat that as the deadline. The owner does the three steps at the bottom himself. Nothing in this repo opens the PR, posts or submits. Rules and the catalog table are in [docs/hackathon.md](docs/hackathon.md).

## Where it stands

| Item | State | What's left |
| --- | --- | --- |
| Plugin on `main`, CI green on Ubuntu, macOS and real Hermes | Done | |
| `hermes plugins install` passes Hermes's scan without `--force` | Done | |
| Catalog entry and PR text | Done | Pinned at `83544d7`, the v0.1.0 commit |
| Run on a live model | Done | Part A is [recorded](docs/live-run.md). It found one bug, fixed in #69 |
| Demo video under 90 seconds | Done | Posted on X with the thread |
| Catalog PR | Open | [NousResearch/hermes-agent#152772](https://github.com/NousResearch/hermes-agent/pull/152772) |
| Post on X | Done | Step 2 |
| Form | Owner | Step 3 |

## 1. Catalog PR

Pick the final commit once nothing else is about to merge, and check CI passed on it:

```bash
git -C /path/to/hermes-superagent fetch origin
sha=$(git -C /path/to/hermes-superagent rev-parse origin/main)
gh run list --repo AirKyzzZ/hermes-superagent --commit "$sha"
git -C /path/to/hermes-superagent checkout "$sha"
hermes plugins validate /path/to/hermes-superagent --install-deps
```

The last line is the check the catalog's CI runs, and it must pass.

In a fork of `NousResearch/hermes-agent`, on a new branch:

```bash
sed "s/0000000000000000000000000000000000000000/$sha/g" /path/to/hermes-superagent/docs/catalog-entry.yaml > plugin-catalog/hermes-superagent.yaml
```

That replaces both the `sha` and the commit in the `image` URL. The PR changes that one file. Its text is the [PR description draft](docs/hackathon.md#pr-description-draft).

## 2. Demo video on X

- Under 90 seconds, showing the plugin working.
- It can say that CI runs the router and worker path on real Hermes with a fake model and that one recorded run used real Hermes and a real model. It can't say a chat message has gone through the router on a live model.
- Posted on X, with @NousResearch tagged in the post.
- Keep the post's URL for the form.

## 3. Form

[The form](https://form.typeform.com/to/FwJLvOln) has these fields, all required. Open the PR and post the video first, because it asks for both links.

| Field | Value |
| --- | --- |
| Plugin Title | `hermes-superagent` |
| Plugin Description | A Hermes plugin for when you run more agents than you can watch: one router you talk to, a worker per task, an outside watchdog that restarts or escalates the stuck ones, and a ledger that keeps every issue, task and Claude Code session until it's really closed. |
| Email Address | The owner's |
| Discord Username | The owner's |
| X Demo Post Link | The post from step 2 |
| GitHub Link to your Hermes Plugin Pull Request | The PR from step 1 |

Between the last two fields, the form shows a reminder to open the catalog PR. It only needs "Continue".
