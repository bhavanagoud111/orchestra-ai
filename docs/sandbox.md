# Test sandbox

The Verification agent can run a repository's tests against a proposed change. This is off by
default (`ORCHESTRA_SANDBOX_ENABLED=false`, or the toggle in Settings).

## How a run works

1. The verification agent fetches the repository's text files (up to `ORCHESTRA_RETRIEVAL_MAX_FILES`)
   and overlays the proposed edits.
2. Files are written to a fresh temporary directory on the API host.
3. `ORCHESTRA_SANDBOX_COMMAND` (default `python -m pytest -q -p no:cacheprovider`) runs in:

```
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
  --cpus 1 --memory 512m --memory-swap 512m --pids-limit 256 \
  -v <tmpdir>:/workspace:ro -w /workspace <image> sh -c "<command>"
```

4. The container is killed after `ORCHESTRA_SANDBOX_TIMEOUT_S` (default 300s). The exit status and
   the last 6,000 characters of output become the `tests` check.

## Dependencies

There is no network inside the container, so the image must already contain everything the
tests need. The default image only adds pytest to `python:3.12-slim`:

```bash
make sandbox-image   # builds orchestra-sandbox-python:3.12
```

For other stacks, build your own image with dependencies pre-installed and set
`ORCHESTRA_SANDBOX_IMAGE` and `ORCHESTRA_SANDBOX_COMMAND`.

## Deployment notes

- The API process needs to reach a Docker daemon. Running the API itself in a container and
  mounting the host's `/var/run/docker.sock` would give it root-equivalent access to the host,
  which is why `docker-compose.yml` leaves the sandbox off. Options: run the API on a VM with
  Docker, use rootless Docker, or a dedicated sandbox host.
- If the sandbox is off or unavailable, verification reports `unverified`, the PR approval
  says so in its risk reasons, and you can require passing tests before any PR is proposed
  (Settings → Require tests for pull requests).
