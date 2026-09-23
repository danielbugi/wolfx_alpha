# CD Design (Not Activated)

> Phase 4A — design only. **No workflow file created in this phase does anything until you
> explicitly activate it.** This document specifies the design precisely enough to implement in
> Phase 4B; it does not implement it. Builds on [CICD_STRATEGY.md](CICD_STRATEGY.md) (Phase 1) and
> the real, validated CI from [CI_IMPLEMENTATION.md](CI_IMPLEMENTATION.md) (Phase 3).

## 1. Conceptual flow (as specified, confirmed feasible)

```
main passes CI (ci.yml, Phase 3 — already real and running)
        │
        ▼
build affected image(s)                         ← reuses docker-validate's build steps/cache scopes
        │
        ▼
push immutable SHA image to GHCR                 ← ghcr.io/<owner>/donchian-{backend,mechanism,frontend}:<git-sha>
        │
        ▼
deployment approval gate                         ← GitHub Environment with required reviewers
        │
        ▼
SSH to VPS
        │
        ▼
pull the new image (by SHA, not `latest`)
        │
        ▼
update ONLY the affected service (docker compose up -d --no-deps <service>)
        │
        ▼
health check (reuse the same Compose healthchecks Phase 2 validated)
        │
        ├── healthy → record CURRENT_SHA / PREVIOUS_GOOD_SHA, deployment complete
        └── unhealthy → do NOT auto-rollback in this first version (see §6) — fail loudly, leave
                          the previous container running (Compose's `up -d` only replaces a
                          container once the new one starts; a crash-looping new container doesn't
                          silently take down a working one under `unless-stopped`, but this is
                          exactly why manual approval + a human watching the first few deploys
                          matters more than automation here)
```

## 2. Why a separate workflow, not an extension of `ci.yml`

`ci.yml` (Phase 3) runs on every PR and push to `main`, has `permissions: contents: read`, and
needs zero secrets — deliberately. A CD workflow needs `packages: write` (to push images) and
access to deployment secrets. Mixing the two would mean every PR run carries elevated permissions
it doesn't need, which is the opposite of least-privilege. **Recommendation: a second workflow file,
`.github/workflows/cd.yml`**, that:
- Triggers only on `workflow_dispatch` (manual) initially — see §3.
- Reuses the same build steps/Dockerfiles/cache scopes as `docker-validate`, so an image built here
  is built exactly the same way CI already proved works — not a parallel, divergent build path.

## 3. Manual approval mechanism — `workflow_dispatch` + GitHub Environments

Both mechanisms evaluated, recommendation given:

| Mechanism | What it does | Verdict |
|---|---|---|
| `workflow_dispatch` | A workflow that only runs when someone manually clicks "Run workflow" (optionally with input parameters, e.g. which service/SHA to deploy) — never fires automatically on push | **Use this as the trigger.** Directly satisfies "I do NOT want every push to main automatically changing production yet." |
| GitHub Environments (`environment: production` on the deploy job) | Lets a repository require one or more specific people to approve a job *after* it's queued but *before* it runs, and can restrict which secrets are visible to that environment | **Use this as the gate**, layered on top of `workflow_dispatch`. Two independent safeguards: (1) a human has to deliberately start the deploy, AND (2) a human (possibly the same person, possibly a required second reviewer if you ever add a collaborator with deploy rights) has to approve it actually running. Also the natural place to scope `DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_USER` as *environment* secrets rather than repository-wide secrets — meaning they're inaccessible to `ci.yml` or any other workflow even if compromised. |

**Recommended design:** `workflow_dispatch` with an input for which image tag (SHA) to deploy,
targeting a job with `environment: production` — this means clicking "Run workflow," picking (or
defaulting to) the latest passing SHA, then a second explicit "Approve and deploy" click on the
Environment's protection rule before anything touches the VPS. Two deliberate human actions, not
one, before production changes.

## 4. Image build and push (the part that CAN reuse Phase 3's work directly)

Same three images, same Dockerfiles, same GHA cache scopes as `docker-validate` in `ci.yml`:

```yaml
# conceptual — not implemented as a file in this phase
- uses: docker/build-push-action@v5
  with:
    context: .
    file: backend/Dockerfile
    push: true                              # the one difference from ci.yml's build
    tags: |
      ghcr.io/<owner>/donchian-backend:${{ github.sha }}
      ghcr.io/<owner>/donchian-backend:latest   # convenience only, never used for the actual deploy
    cache-from: type=gha,scope=backend
    cache-to: type=gha,mode=max,scope=backend
```

`push: true` is the only structural difference from what Phase 3 already built and validated —
everything else (Dockerfile, build context, cache scope) is identical, so there is no new,
unvalidated build path to trust.

## 5. SSH deploy step (design, not implemented)

```bash
# conceptual — this exact command sequence, not yet wired into a workflow file
ssh "$DEPLOY_USER@$DEPLOY_HOST" "
  cd /opt/donchian/compose &&
  export IMAGE_TAG=${GIT_SHA} &&
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file /opt/donchian/env/.env \
    pull backend &&
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file /opt/donchian/env/.env \
    up -d --no-deps backend &&
  echo ${GIT_SHA} > /opt/donchian/CURRENT_SHA
"
```

`--no-deps` is deliberate and important: it stops Compose from also recreating `postgres` (or any
other dependency) as a side effect of updating `backend` — exactly the "restart scope" principle
already established in `CICD_STRATEGY.md` §5 ("never a blanket `up -d` for every service").

## 6. Rollback design — intentionally simple (full detail in `PRODUCTION_MIGRATION_RUNBOOK.md` §ROLLBACK)

Per your explicit instruction, **no automatic rollback in this first version.** Design:

- The VPS keeps two small text files: `/opt/donchian/CURRENT_SHA` and
  `/opt/donchian/PREVIOUS_GOOD_SHA`, updated by the deploy step only after a successful health
  check.
- **Manual rollback is a single SSH command**, not a workflow:
  ```bash
  ssh "$DEPLOY_USER@$DEPLOY_HOST" "
    cd /opt/donchian/compose &&
    export IMAGE_TAG=\$(cat /opt/donchian/PREVIOUS_GOOD_SHA) &&
    docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file /opt/donchian/env/.env \
      up -d --no-deps backend
  "
  ```
- **A database migration is never assumed reversible by an application rollback.** This is the
  most important rule in this whole document: if a deploy included a schema change (a new
  `add_*.sql` file applied), rolling the *application* image back to the previous SHA does **not**
  undo that schema change. Per `INFRASTRUCTURE_PLAN.md` §6.4 (Phase 1, unrevised): migrations must
  stay additive-only (new tables/nullable columns) specifically so an application rollback never
  needs a matching schema rollback. If a genuinely destructive migration is ever unavoidable, it
  must be a separate, explicitly-reviewed decision with its own rollback plan — never bundled into
  a routine deploy.
- Full step-by-step rollback procedure, including the database-specific caveats, is written out in
  `PRODUCTION_MIGRATION_RUNBOOK.md`'s dedicated ROLLBACK section rather than duplicated here.

## 7. What is explicitly deferred past this first version

- Automatic rollback on failed health check (Phase 1's `CICD_STRATEGY.md`/`INFRASTRUCTURE_PLAN.md`
  described this as the eventual target — deliberately not built yet, per your explicit "keep
  rollback simple initially" instruction).
- Automatic deploy on every push to `main` (deliberately requires manual `workflow_dispatch` +
  Environment approval instead).
- Any deploy trigger for `bot`/`pipeline`/`channel-sender` beyond what's specified here — the same
  mechanism applies to all four images (backend, mechanism [serving bot/pipeline/channel-sender],
  optionally frontend if the Option A self-hosted fallback is ever used instead of Vercel), but this
  document focuses the worked example on `backend` for clarity; the same `--no-deps <service>`
  pattern applies uniformly to each.

## 8. Nothing in this document has been activated

No `.github/workflows/cd.yml` file exists yet. No image has been pushed to GHCR. No GitHub
Environment has been configured. No SSH key has been generated. This is the specification Phase 4B
would implement, pending your separate approval to proceed past Phase 4A.
