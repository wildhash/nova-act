# Nova Hackathon Assistant Setup

This guide configures the production sample:
- Nova 2 Lite tool orchestration
- Gmail and Calendar tools via Google APIs
- Nova 2 Sonic stream bootstrap
- Nova Act browser fallback
- Optional OS desktop control

## 1) Install optional dependencies

```bash
pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt
```

## 2) Store Google OAuth client JSON in AWS Secrets Manager

Create secret name: `nova-assistant/google-client`

Secret value must be the exact Google OAuth client JSON object from Google Cloud Console.

## 3) Bootstrap Google user token

Run once with interactive auth enabled:

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive False \
  --text "Find emails from alice@example.com about Q3 report" \
  --allow_interactive_google_auth True
```

This writes refreshed token JSON into secret: `nova-assistant/google-token`.

## 4) Run in normal mode

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant --interactive True
```

## 4b) Run in full auto mode

This mode keeps planning and executing until completion token is emitted or max steps is reached.

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive False \
  --full_auto True \
  --goal "Find emails from alice@example.com about Q3 report, create a calendar review meeting with Bob tomorrow at 10am, then open Gmail and show the matching thread" \
  --max_auto_steps 12 \
  --allow_os_control True
```

Audit log file (JSONL): `/tmp/nova-assistant/audit.jsonl`

Optional cloud audit sinks:

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive False \
  --full_auto True \
  --goal "Run daily inbox triage" \
  --audit_cloudwatch_log_group "/nova/assistant/audit" \
  --audit_cloudwatch_log_stream "prod-worker-1" \
  --audit_s3_bucket "my-audit-bucket" \
  --audit_s3_prefix "nova-assistant/prod"
```

Interactive shortcut:

```text
/auto <goal text>
```

## 5) Validate Sonic stream bootstrap

Inside interactive shell, run:

```text
/voice-start
```

Or from CLI:

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive False \
  --text "status" \
  --start_sonic_stream True
```

## 5b) Run live Sonic mic + speaker loop

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive False \
  --text "status" \
  --start_sonic_live True \
  --sonic_live_seconds 20 \
  --sonic_input_rate 16000 \
  --sonic_output_rate 24000
```

Interactive shell shortcut:

```text
/voice-live 20
```

## 6) Enable OS control only on trusted hosts

Default is disabled. To enable:

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive True \
  --allow_os_control True
```

Policy guardrails (allow/deny):

```bash
PYTHONPATH=src python -m nova_act.samples.nova_hackathon_assistant \
  --interactive True \
  --allow_os_control True \
  --os_allowed_actions "move_mouse,click,type_text,screenshot" \
  --os_denied_actions "hotkey,press" \
  --os_denied_hotkeys "alt+tab,alt+f4,ctrl+alt+delete,cmd+q,win+r" \
  --os_max_type_chars 400 \
  --os_max_scroll_abs 1200
```

OS actions exposed to the model:
- move_mouse
- click
- type_text
- hotkey
- press
- scroll
- screenshot

## Security guidance

- Run desktop control on isolated worker hosts only.
- Keep least-privilege IAM on Bedrock and Secrets Manager resources.
- Keep Google scopes minimal and rotate credentials regularly.
- Keep Nova Act browser runs in dedicated profiles and reviewed prompts.
- Treat `allow_os_control=True` as privileged mode; run it only on isolated automation workers.
