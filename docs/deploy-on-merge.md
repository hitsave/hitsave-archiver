# Deploy on merge

When a pull request merges into `main`, saturn deploys it within about 5 minutes of its checks
passing. You don't need to pull the checkout on saturn, rebuild images, push Windmill jobs or
deploy the ledger by hand.

## How saturn finds new commits

Saturn checks GitHub itself. The `hitsave-deploy` container runs
`scripts/deploy/deploy-poll.sh` every 5 minutes. The poller:

1. fetches `main` and stops if it is the commit deployed last;
2. asks GitHub for that commit's checks. It waits while they run, and never deploys a commit
   whose checks failed. A commit with no checks gets 10 minutes for them to appear, because
   some changes, such as docs, start no checks at all;
3. runs `scripts/deploy/deploy-saturn.sh` from that commit.

Saturn only reads from GitHub. Nothing on GitHub, including a pull request or a workflow, can
start anything on saturn. Only a commit on `main` gets deployed, and only people with write
access to the repo can add one.

## What a deploy does

`deploy-saturn.sh` runs these steps in order and stops at the first one that fails:

1. It waits until no sweep or prune is running, then holds the remote-intake lock until it
   finishes, so no sweep starts in the middle of a deploy.
2. It fast-forwards the live checkout on saturn to the new commit.
3. It rebuilds the `ingest-worker` and `omeka-catalog` images if anything under
   `ingest-worker/`, `scripts/`, `packages/` or `docker-compose.yml` changed.
4. It makes Windmill match `windmill_ops/wmill/` if any job file changed. It pushes changed
   scripts and schedules, and deletes scripts and schedules that git no longer has.
5. It deploys the new ledger image on luigi if the ledger's inputs changed. These are the same
   paths that make the `ledger-image` workflow build a new image. The deploy waited for that
   build in step 2 of the poller, because the build is one of the commit's checks.

The deploy compares the new commit with the last commit it deployed, so if several merges
land together, one deploy covers all of them.

## Check that a deploy worked

Each deploy writes its full output to
`hitsave-archiver/.generated/deploy/<time>-<commit>.log`. The last line of a good deploy is
`== Done`. The same folder holds:

- `deployed-commit`: the last commit deployed;
- `failed-commit`: a commit whose deploy or checks failed. The container leaves it alone.

To see what the container did recently:

```bash
docker logs --since 24h hitsave-deploy
```

It logs only when something happens, such as a deploy or a commit whose checks failed.

## When a deploy fails

The log names the step that failed. The container won't try that commit again, so a broken build
doesn't run every 5 minutes. Fix the cause and do one of these:

- Merge a fix. The container deploys the new commit and catches up on everything since the last
  good deploy.
- Run the deploy by hand from the checkout on saturn:

  ```bash
  HITSAVE_ARCHIVER_DIR=$PWD scripts/deploy/deploy-saturn.sh origin/main
  ```

Common causes:

- **"the live checkout has uncommitted changes to tracked files"**: someone edited a tracked
  file in the checkout on saturn. Commit or remove the change.
- **"held the remote-intake lock for over 7200s"**: a sweep ran for more than 2 hours.
- **"its checks failed on GitHub"**: the commit's tests or image build failed. Fix it on
  `main`.

## What still needs a person

- Images from other repos: the intake image (`hitsave-intake`) and the DVD menu tool
  (`hitsave-dvd-menu-preview`).
- The Windmill containers themselves. After a change to `windmill_ops/docker/`, recreate the
  worker by hand: `docker compose up -d --no-deps windmill_worker` in `windmill_ops/docker`.
- Private config in `hitsave-archiver-config`, including the live `ledger.yaml` on luigi.
- Omeka, the theme and the viewer module.
- The deploy container itself. After a change under `deploy/`, run
  `scripts/deploy/start-deploy-container.sh` again. A change to `scripts/deploy/deploy-poll.sh`
  or `deploy-saturn.sh` needs nothing: the container reads both from the checkout.

## Start the deploy container

Run this on saturn from the live checkout:

```bash
scripts/deploy/start-deploy-container.sh
```

It builds the image `hitsave/deploy:local` from `deploy/` and starts the container
`hitsave-deploy`, which restarts on its own after a reboot. The container:

- runs as `jonas` (user 1000) with the `docker` group, and uses the host's Docker socket to
  build images and run the Windmill CLI and the ledger deploy;
- mounts the live checkout and `hitsave-archiver-config` at their host paths, so every path
  means the same inside and outside the container;
- reads the checks from GitHub's public API without a token.

To pause deploys, run `docker stop hitsave-deploy`. To start them again, run
`docker start hitsave-deploy`.
