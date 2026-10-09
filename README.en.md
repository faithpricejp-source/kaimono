# 買物 (kaimono)

[中文](README.md) | English

A "buying things" app that serves only my own interests: it records orders, reminds me to be home for deliveries based on the delivery time, summarizes the status of the stock- and price-watching scripts I wrote myself, and has AI recommend products and services based on my own preferences and spending history — no affiliate kickbacks, no ads.

On macOS it is a WKWebView shell (`買物.app`) backed by a Python standard-library server that listens only on `127.0.0.1`; a phone or tablet can reach the same page in a browser over my own private network (e.g. Tailscale).

## Why I built this

This is one app in a "personal operating system" series. The series has a single goal: to keep in my own hands every action and the history of every exchange of information between me and the outside world — what I looked at, where I lingered and for how long, what I clicked, what I bought, what I sold, and what I did after seeing it.

An ordinary person's behavior is shaped by all kinds of known and unknown conditions, environments, and stimuli, leaving them in a pseudo-random state — as if other people had installed switches on us: flip this switch, pluck that string, and we perform the action they expected. To ourselves it feels random; in other people's eyes we look like puppets on strings. Platforms and institutions hold our behavioral data and understand us better than we understand ourselves. This whole system exists to change that.

The idea behind it: an individual trying to understand the outside world is like a drop of water trying to understand the ocean — practically impossible. But a drop of water can look inward and see clearly how each of its own molecules moves — how I will move at a given temperature, and how I will move when I meet a given tide or current. Once that is clear, I can secure better conditions for my own survival. This is far more realistic than understanding the whole ocean.

The drop's own state needs to be recorded too. The same person is noticeably more likely to get a major decision wrong after a bad night's sleep, after arguing with family that day, or when feeling unwell; many people's memoirs describe moments like these. So besides recording actions, I also record my physical condition, mood, and external environment at the time (weather, markets, schedule), so that later I can see "in what state, what I tend to do."

The back ends of these apps are ultimately meant to be connected and share information with one another. Large financial groups have long treated every customer this way: they look at the customer's behavior across deposits, loans, insurance, and securities together, then cross-sell. With AI, there is no reason an individual can't do the same for themselves — the difference being that this time, the data and the analysis serve only me. For now each app uses its own local SQLite database; connecting them is the next step.

The ideal end state: all information that can reach me must first pass through a filter and a recorder I built myself before it gets in; my feedback and behavior must likewise pass through my own filter and protector before they go out.

So the shared convention of this series is: all behavior records are written to a local database and never uploaded to any third party; AI analysis runs locally or on a service the user chooses.

## Features

- **Orders and delivery reminders**: Log orders by hand (merchant, item, delivery date, time window, tracking number). On delivery day it reminds you twice, 2 hours and 20 minutes before the time window (system notification + `say` read-aloud + optional email); orders with only a date get one reminder at 09:00 that day.
- **Watch list**: A read-only view of the status of the sentinel scripts you wrote yourself (whether launchd is running them, their state file, the last line of their log); the list lives in `watchers.json`.
- **Recommendations**: Calls the local `claude` CLI, which searches the web and produces 6–8 recommendations; each must cite a source URL, which the server verifies can be opened. Then each item is price-checked (current price, shipping, total landed price), and every recommendation's reasoning must name its basis (the preference profile, spending history, or past feedback).
- **Event table**: Every action in the app is written to the `events` table, so I can analyze myself later.

## Running

Requirements: macOS, Python 3.11+ (standard library only); the recommendation feature needs a logged-in [Claude Code](https://claude.com/claude-code) CLI.

```sh
python3 server/app.py              # server: http://127.0.0.1:8470/
python3 server/remind.py           # delivery reminders (suggest running every 5 minutes via launchd)
python3 server/recommend.py generate --if-stale   # produce a batch of recommendations
zsh macapp/build.sh                # build build/買物.app
python3 -m pytest -q               # tests
```

`launchd/com.example.shopping.plist` is an example for running the server as a resident service.

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `SHOPPING_DATA` | `~/Library/Application Support/shopping` | Data directory (SQLite, preference profile, watchers.json) |
| `SHOPPING_PORT` | `8470` | Server port |
| `SHOPPING_LEDGER` | `<data dir>/ledger.csv` | Optional household spending ledger CSV; recommendations read a summary of it |
| `SHOPPING_CLAUDE` | `claude` on `PATH` | Path to the Claude Code CLI |
| `SHOPPING_CITY` | `东京` (Tokyo) | Which city seasons and shipping are calculated for |
| `SHOPPING_SAY_VOICE` | `Tingting` | Voice used to read reminders aloud |

Email reminders are optional: put a `notify.py` that provides `send_email(subject, body)` on your `PYTHONPATH`; without it, email is skipped.

## Privacy

Orders, feedback, and events are all stored in local SQLite. When generating recommendations, the preference profile, a feedback summary, a spending-history summary, and in-transit orders are sent to the Claude you configured; if you don't want to send them, don't run recommendations.

## Limitations

This is a tool the author uses daily on their own machine, written to their own habits (Chinese interface, Japanese shopping and logistics). Feel free to take it and modify it.

## License

GPL-3.0
