# Hashbot

Automated Bitcoin hashrate arbitrage for the [Braiins Hashpower Market](https://hashpower.braiins.com).

Self-hosted · Mobile-first dashboard · One-command Docker deploy · API key encrypted at rest.

---

## What it does

Hashbot monitors the Braiins Hashpower Market orderbook every 2 minutes and keeps your bid at the optimal price automatically. Set which rank to target (default: 5th lowest active bid + 1 tick) and Hashbot handles the rest — including auto top-up when your budget runs low.

## Features

- Automated bid price tracking and updates on a configurable interval
- Strategy: track the *N*th lowest active bid + configurable tick offset
- Auto top-up when remaining budget drops below threshold
- Real-time mobile-first dashboard (Material Design 3, Bitcoin orange)
- Price history chart — 1D / 1W / 1M / 6M
- API key validated against Braiins on entry, then encrypted with AES-128 (Fernet) and stored on disk — never in env vars, config files, or logs
- Timezone-aware activity log and timestamps

---

## Requirements

- Docker 24+ with the Compose plugin (`docker compose version` to check)
- A Braiins Hashpower Market account with an active bid

---

## Quick Start

### 1. Get the files

```bash
git clone https://github.com/m1xb3r/braiins-hashbot.git
cd braiins-hashbot
```

### 2. Set up

```bash
make setup
```

Checks all required files are present and creates `.env` from `.env.example`.

### 3. Start

```bash
make up
```

### 4. Connect

Open **http://localhost:8000** in your browser.

A setup prompt appears on first boot — paste your Braiins Owner Token and click **Validate & Connect**. The key is validated live against the Braiins API, then encrypted and stored in the Docker volume. The engine starts automatically once the key is saved.

---

## Configuration

Everything is configurable through the dashboard UI under **Settings**:

| Setting | Default | Description |
|---|---|---|
| Bid Rank Target | 5 | Track the Nth lowest active bid |
| Price Offset | +1 tick | Ticks above the target bid (1 tick = 1,000 sat) |
| Price Unit | EH | Display prices in sat/EH/day or sat/PH/day |
| Timezone | Europe/Berlin | Timezone for all dashboard timestamps |

Operational parameters (poll interval, top-up threshold, speed limit) are managed internally and synced automatically from your live Braiins bid on first connect.

---

## Commands

| Command | Description |
|---|---|
| `make up` | Build and start all services |
| `make down` | Stop everything |
| `make restart` | Restart (picks up settings changes) |
| `make logs` | Tail logs from both containers |
| `make logs-engine` | Engine logs only |
| `make logs-dashboard` | Dashboard logs only |
| `make shell-engine` | Debug shell in engine container |
| `make build` | Force rebuild after code changes |
| `make clean` | Remove containers, image, and data volume |

---

## Architecture

```
braiins-hashbot/
  engine     — main.py      polls orderbook every 2 min, updates bid, auto top-up
  dashboard  — dashboard.py FastAPI + UI on :8000

Shared Docker volume at /data:
  config.json           runtime config
  master.key            Fernet encryption key (chmod 600)
  api.key               encrypted Braiins API key
  price_history.json    bid price time series
  engine_state.json     cooldown / error state
  settings.json         bid rank, tick offset, display unit, timezone
  hashbot.log           rotating activity log (1MB x 2)
```

Both containers share a single named Docker volume. The API key is the only credential — it never touches an environment variable, config file, or log line.

---

## Security

- API key encrypted at rest with [Fernet](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256)
- Machine-specific `master.key` generated on first boot, stored in the Docker volume
- Key file overwritten with zeros before deletion
- `ScrubAPIKeyFilter` strips the key from all log output
- Container runs as non-root user (`hashbot`)
- No sensitive values in environment variables or `config.json`

---

## Contributing

Pull requests welcome. Please open an issue first for significant changes.

---

## License

MIT

---

## Donations

If Hashbot saves you money on hashrate, consider sending some sats back.

**Onchain BTC**
```
bc1qjsddvw9f6kqxnh4ghl48n3r6yyucepp7fq5ryj
```

**BOLT 12**
```
lno1pgwyyunpd95kuueqfpshx6rzda6zqstswpex2cmfv96xjmmwzrhq8pjw7qjlm68mtp7e3yvxee4y5xrgjhhyf2fxhlphpckrvevh50u0qtw7d24ghahyj4gv83gvjx7zcsv5e9jr6mcq3ftryplvm3x0ufpfvqszw8dj773h9mz0mfzymfzrm8rnt2znccv8uae3n3kpfzrupyrynqzqqvcycewwz8c3j3x3lz3jnaafftva06hsgyf5nmdzcn3j6el374d0q84wfn5druwjn7ng32f5vkzqaa9qg2ntqvvjs0gr7d8uhde2sd5w86durjnnlxajcp2eyls6e37x8zndl9wpvqpj9h77mzzjch5an4xajfxkz5kxuzpw9zfql7kjkdq6n4jdav3cn3mnd46n0zkyh5uxkkqz9jc7qfazv5nz
```
