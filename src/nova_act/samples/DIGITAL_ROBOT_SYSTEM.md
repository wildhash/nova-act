# Digital Robot Runtime (Nova)

This sample turns Nova into a durable "digital robot in a computer" runtime.

Default profile is tri-domain: browser + workspace APIs + desktop control.

## What it adds beyond a one-shot assistant

- Mission model with explicit objective, constraints, and success criteria.
- O-P-A-V-L execution loop:
  - Observe
  - Plan
  - Act
  - Verify
  - Learn
- Checkpoint persistence for pause/resume workflows.
- `supervised` mode with human approval gates.
- `autonomous` mode for fully automated cycle execution.
- Voice bootstrap and live audio loop commands from the runtime shell.
- Explicit full-auto toggle from a supervised default.

## Why this matters beyond hackathon scope

- Missions become reproducible and auditable.
- Work can resume after interruptions/failures.
- Guardrails remain centralized in `nova_hackathon_assistant.py`.
- New capabilities can be added as tools without redesigning the runtime loop.

## Quick start

Install optional dependencies first:

```bash
pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt
```

Run interactive shell:

```bash
PYTHONPATH=src python -m nova_act.samples.digital_robot_system --interactive True --mode supervised
```

Shell commands:

- `/run <objective>`
- `/resume`
- `/auto-on`
- `/auto-off`
- `/voice-status`
- `/voice-start`
- `/voice-live <seconds>`
- `/exit`

The runtime starts supervised-first. Use `/auto-on` only when you want explicit full-auto execution.

If your environment does not support Bedrock bidirectional streaming yet, voice commands will now return a structured error instead of crashing the shell. Check support with `/voice-status`.

Typical remediation:

```bash
pip install -U boto3 botocore
```

## Non-interactive mission run

```bash
PYTHONPATH=src python -m nova_act.samples.digital_robot_system \
  --interactive False \
  --mode autonomous \
  --objective "Review unread Gmail messages from alice@example.com and create a calendar follow-up for tomorrow" \
  --constraints "prefer api tools first,do not use os control unless needed" \
  --success_criteria "email summary produced,calendar event created" \
  --max_cycles 8
```

Use mission packs:

```bash
PYTHONPATH=src python -m nova_act.samples.digital_robot_system \
  --interactive False \
  --mode supervised \
  --mission_pack tri-domain \
  --deadline_days 3 \
  --objective "Operate inbox, browser workflows, and desktop app tasks with cowork collaboration"
```

Supported mission packs:

- `tri-domain` (default): browser + workspace + desktop.
- `browser`
- `workspace`
- `desktop`

Cowork protocol in model responses:

- `COWORK_REQUEST: <exact action user should take>`
- `BUG_REPORT: <issue and likely cause>`

## Checkpoint file

Default checkpoint path:

- `/tmp/nova-assistant/digital-robot-checkpoint.json`

Override with `--checkpoint_path`.

## Suggested next extensions

- Tool plugin registry with per-tool policy tags.
- State graph memory (task graph + dependency edges).
- Multi-agent decomposition (planner, executor, verifier).
- Replay engine for deterministic postmortem analysis.
