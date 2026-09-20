# The NeuroCode action

Run NeuroCode from a GitHub workflow: compile a requirement, hand a plan to the agents, wait on a
run — and **fail the job when it needs a person**.

It is `nc`, the same terminal client, installed from this repository's own `cli/` and pointed at a
server with a personal access token. There is no second API and no privileged path: the token acts as
the person who made it, cut to the scopes it names, and every gate, tool rule and signature is still
the server's. What this action adds over a `curl` is that it understands the answer — `nc` exits `3`
when a run has stopped at a gate, a permission card or your signature, and `4` when watching gave up
while the run was still going, and a workflow that read either as success would be a green tick over
work nobody has looked at.

## What it needs

The server has to be reachable from the runner. A NeuroCode on your laptop is not — it listens on
`127.0.0.1`. Either host one (`deploy/` is a Docker Compose stack with Postgres, the API and Caddy
for HTTPS) or point the action at a self-hosted runner on the same network as a server started with
`NEUROCODE_LISTEN_ON_LAN=true`. Two secrets:

| Secret | What |
| --- | --- |
| `NEUROCODE_SERVER` | `https://neurocode.example.com` — the web app's address (its API is under `/api`) or an API's own. |
| `NEUROCODE_TOKEN` | A personal access token from Settings → Access tokens. **Never a password.** |

A token made with no scopes carries everything its person holds **except `machine:access`**, so it
cannot open a shell on the server's machine. Give it `plans:compile`, `plans:decide` and `runs:run`
and it can do exactly what this action offers and nothing else.

## Inputs

| Input | Means |
| --- | --- |
| `server`, `token` | Required. From secrets. |
| `command` | `status`, `compile`, `dispatch`, `wait`, `approvals`, or `nc` to pass your own arguments. |
| `project` | The project id. Required by `compile`; the default for the rest. |
| `requirement` / `requirement-file` | `compile`: what you want built, in your words, or a file in the checkout. |
| `plan` | `dispatch`: the plan (`PLAN-…`). |
| `goal-budget` | `dispatch`: run again until every acceptance criterion is met, at most this many attempts. |
| `run` | `wait`: the run to follow (`RUN-…`). A `dispatch` step's `ref` output is one. |
| `timeout` | `wait`: seconds before it gives up watching. Default 900. Giving up stops nothing. |
| `args` | `command: nc`: the arguments, e.g. `memory search "rounding"`. `--json` is added. |
| `fail-on-waiting` | Default `true`. Set `false` to carry on and read the `waiting` output yourself. |

## Outputs

`json` (what `nc --json` printed, unchanged), `ref` (`PLAN-…` or `RUN-…`), `status`, `waiting`
(`true` when a person is needed) and `exit-code` (nc's own: 0 done, 1 refused, 2 usage, 3 waiting on
a person, 4 watch timed out).

## A workflow

An issue labelled `neurocode` becomes a plan, the plan goes to the agents, and the job waits — and
goes red the moment the run reaches your signature, with nothing merged.

```yaml
name: neurocode

on:
  issues:
    types: [labeled]

permissions:
  contents: read

jobs:
  build-it:
    if: github.event.label.name == 'neurocode'
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4

      - name: Compile the issue as a requirement
        id: plan
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: compile
          project: PRJ-1
          requirement: ${{ github.event.issue.title }} — ${{ github.event.issue.body }}

      - name: Hand it to the agents
        id: run
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: dispatch
          plan: ${{ steps.plan.outputs.ref }}

      - name: Wait for it
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: wait
          run: ${{ steps.run.outputs.ref }}
          timeout: '2700'
```

From another repository, the same steps with
`uses: neurosynthlabs/neurocode/.github/actions/neurocode@main` — the checkout of this repository
that carries the action carries the `nc` it installs, so the two can never be different versions.

Reading the outputs instead of failing:

```yaml
      - name: Wait for it
        id: wait
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: wait
          run: ${{ steps.run.outputs.ref }}
          fail-on-waiting: 'false'

      - if: steps.wait.outputs.waiting == 'true'
        run: echo "::notice::${{ steps.run.outputs.ref }} is waiting on a signature."
```

And a gate check on a schedule, which fails while anything sits in the approvals inbox:

```yaml
      - uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: approvals
```

## What it will not do

`accept`, `approve`, `deny`, `merge`, `push` and `send-back` are refused, including through
`command: nc`. `nc` has no `--yes` and never will — it would mean a signature nobody read and a merge
nobody watched — and a workflow allowed to type `nc accept` would be that `--yes` by another route.
A workflow may compile, dispatch and wait; signing a run, answering a gate and merging happen where
the diff, the review and the findings are.

## Working on it

`run.sh` is the body, and `test/action.test.mjs` runs it here against a stub `nc` — including the
case that matters, exit code 3.

```sh
node --test .github/actions/neurocode/test/
```

Linux and macOS runners. The script needs `bash` and `python3`, which both have.
