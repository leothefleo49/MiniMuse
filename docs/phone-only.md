# Phone only, no computer

nanoMuse can live on one Android phone: the agent, its data and the model that answers.
No computer, no relay and no account are needed, and after the first setup the phone works
with no internet. This page is for the two routes, what each one can do, and what to do when
a computer that used to run nanoMuse is gone.

The first setup downloads software and a model, so it needs Wi-Fi or a mobile connection
once. A model is several gigabytes; after it is on the phone nothing else is fetched.

If a computer already has the model, copy it instead of downloading it again: Ollama keeps
models in `models` under its data folder (`C:\Users\<you>\.ollama` on Windows,
`~/.ollama` on macOS and Linux). Copy that whole `models` folder to `~/.ollama/models`
inside Termux on the phone, then pass `--skip-model` to the installer. The manifests and
blobs are portable; Ollama finds the model without a download. This is the fastest way on
a metered connection.

## What you need

- An Android phone with arm64 and Android 8.0 or newer. 6 GB of memory is the comfortable
  minimum for the default 4B model; 4 GB phones can use the 3B model; 10 GB and more can
  use the 8B model.
- [Termux](https://f-droid.org/packages/com.termux/) from F-Droid, not from Google Play.
  The Play build is old and its packages do not match.
- About 4 GB of free storage for the default setup, and more for files and chats.
- Optional: [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) to start nanoMuse
  after a restart, and [Termux:API](https://f-droid.org/packages/com.termux.api/) to put
  reminders and questions in the phone's notification shade.
- The iPhone app has no local server: iOS does not let an app keep one running. An iPhone
  needs a model endpoint on another device, the relay, or a key of its own. The rest of this
  page is about Android.

## Two ways to carry it

| | The app and a model | The runtime and the web app |
|---|---|---|
| What runs on the phone | The nanoMuse app's own agent, and Ollama serving the model | Ollama, the Python runtime, and the web app in the browser |
| What you see | The Android app, with its Hands, notifications and phone tools | The same web app as the desktop, installed to the home screen |
| Best for | A new phone with no history, and the phone's own calendar, contacts, alarms and screen | Keeping the chats, goals, memory and files from a computer, or using the web app |
| Data | The app's own data, and the account's sync when signed in | `~/.nanomuse` and its workspace, on the phone |

Both routes use [Ollama](https://ollama.com) as the model server. It runs on the phone and
speaks the OpenAI-compatible API the app and the runtime already know.

## Route A: the app and a model on the phone

Install the app from the [latest release](https://github.com/nano-muse/nanoMuse/releases/latest).
It works signed out with a model of your own; an account is only needed for the relay's
models, sync and the hub.

In Termux, one command installs Ollama and a model that fits the phone:

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/scripts/phone/install.sh | bash -s -- --app
```

The script picks the largest model the phone's memory can hold, downloads it and starts
Ollama. To choose another model, pass `--model`:

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/scripts/phone/install.sh \
  | bash -s -- --app --model qwen2.5vl:3b
```

Then, in the app:

1. Open *Settings, Models, Chat, Add a provider, Ollama*.
2. Keep the address `http://127.0.0.1:11434/v1` and pick the model the installer printed,
   for example `qwen3:4b`.
3. For *Operating the screen*, add a model that reads pictures, such as `qwen2.5vl:3b`.

The address is the phone itself. The app sends the conversation to Ollama over the
loopback interface, so no other device and no other app can read it.

Keep Termux open while the app works. Android stops background apps on its own unless you
allow it: open Android's battery settings, find Termux, and choose *Unrestricted* (the name
differs by vendor). Install Termux:Boot so Ollama comes back after a restart, and keep
Termux:API installed if you want the app's notifications and the runtime's notifications
to reach the shade. The app's own notifications work without Termux:API.

## Route B: the runtime and the web app on the phone

This route runs the same Python runtime as the desktop, with the same chats, goals, memory,
feed, skills and web app. It is the way to keep using a computer's data after the computer
is gone, provided the data was exported first.

In Termux:

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/install.sh | bash
```

The installer:

- installs Ollama and a model that fits the phone;
- installs a small Debian container with `proot-distro` and the Python runtime inside it;
- points the runtime at the phone's Ollama, with the server on `127.0.0.1`;
- writes the `nanomuse-phone` command and, when Termux:Boot is installed, a boot script.

When it finishes, open `http://127.0.0.1:8787/` in the phone's browser and choose *Add to
home screen*. The web app is served from the phone; the service worker keeps the shell, so
it also opens with no network. The access token stays on: the browser keeps it, and the
server only listens on the loopback address.

To install from a checkout instead of cloning the repository, for example a fork with your
own changes:

```bash
bash scripts/phone/install.sh --runtime --source /sdcard/nanoMuse/repo
```

The source is copied into the Debian container, so the phone does not need the repository
on its storage afterwards. `--repo URL` clones another repository instead.

### The commands

The installer puts `nanomuse-phone` in Termux's `PATH`:

| Command | Does |
|---|---|
| `nanomuse-phone up` | Starts Ollama and the runtime, and takes a Termux wake lock |
| `nanomuse-phone down` | Stops both |
| `nanomuse-phone restart` | Stops and starts both |
| `nanomuse-phone status` | Says whether the model and the app answer |
| `nanomuse-phone open` | Opens `http://127.0.0.1:8787/` |
| `nanomuse-phone logs` | The last lines of the runtime and Ollama logs |
| `nanomuse-phone update` | Pulls the source, installs it again and restarts |
| `nanomuse-phone model NAME` | Downloads another Ollama model, selects it and restarts |
| `nanomuse-phone restore FILE` | Stops the app, restores a data export, and starts it again |

A small supervisor starts the runtime again if it exits, and the wake lock asks Android to
keep Termux alive while it works. Android can still stop Termux under memory pressure or an
aggressive battery manager; open Termux and run `nanomuse-phone up` if the app stops
answering.

### Restoring a computer's data

On the computer, before it is gone, open *Settings, Download your agent data* in the web
app. Copy the zip to the phone. Run `termux-setup-storage` once in Termux and allow it, so
Termux can read Downloads, then run:

```bash
nanomuse-phone restore ~/storage/downloads/nanoMuse-data.zip
```

The restore accepts only the names a nanoMuse export writes: chats, sessions, memory,
goals, reminders, triggers, the profile, the feed, ideas, contacts, skills, traces and the
audit log. It never replaces the phone's `config.toml`, the vault or the server token. The
export leaves secrets and settings out on purpose, so enter API keys and connectors again
in the app. Workspace files are not in the export either: copy the computer's `workspace`
folder into `~/.nanomuse/workspace` on the phone if you want those files.

If the computer is already gone and no export exists, the chats and memory on it cannot be
recovered. This page cannot bring them back; it only makes sure the next phone is not
dependent on a computer.

The export above leaves out the One For All databases, the workspace files, the vault and
the server token. To move the whole install, package it on the computer:

```bash
nanomuse phone package --out nanoMuse-bundle.tar.gz
```

Copy `nanoMuse-bundle.tar.gz` and `scripts/phone/install.sh` to the phone, then in Termux:

```bash
bash scripts/phone/install.sh --runtime --bundle /sdcard/Download/nanoMuse-bundle.tar.gz --model qwen3:4b
```

The bundle carries the checkout, the chats, memory, goals, the workspace files, the vault
and the settings, so the phone opens exactly as the computer did. Pass `--model` to choose
the model the phone runs; a computer's large model may be too big for a phone.

## Pair two runtimes with no relay

Two runtimes can sync their chats straight to each other: no relay, no account. On each
device, from a terminal:

```bash
nanomuse pair add http://192.168.1.20:8787 --token <the other device's server_token>
nanomuse pair list
nanomuse pair remove <name>
```

The token goes into the encrypted vault; `pairs.json` keeps only the address and a vault
reference. While both devices are online (the same Wi-Fi, or any path that reaches the
address), the chats sync on their own: each device pushes its new messages to the other
and pulls what arrived there, the main chat is one conversation for the pair, and renames
and deletes travel too. The address is the one `nanomuse serve` prints on the other
device, and the token is in that device's `data/server_token`. Every runtime serves the
same sync protocol the relay speaks, so a pair works exactly like the account sync, minus
the server in the middle.

## Offline, and what still needs the internet

Without a network, on a phone that was set up as above:

- Chat with the local model, including picture messages when the chosen model reads
  pictures.
- Memory, goals, reminders, routines, the feed, ideas, files, skills and the Library.
- The web app's shell and its saved pages. The service worker also keeps the last good copy
  of API reads, so the app opens on a cold start with the network down.
- Notifications through Termux:API while Termux is alive. Web Push needs a push service on
  the internet and is not used in the phone-only setup.

Still need the internet, by their nature:

- nanoMuse Cloud models, the relay, sign-in, conversation sync and the hub.
- Mail, calendar feeds, contacts that come from a server, web search and update checks.
- A model that has not finished downloading.
- Termux package updates and `nanomuse-phone update`.

The phone-only configuration turns the update check off, sets the relay as not required,
turns conversation sync off and disables the hub, so the runtime does not wait on a network
it does not have.

## Memory, battery and notifications

- A 4B model needs about 3 GB of memory while it answers; the 8B model needs about 6 GB.
  Android may stop other apps to make room. Closing heavy apps helps.
- A wake lock keeps the CPU available for scheduled work; it costs battery.
  `nanomuse-phone down` releases it.
- Termux:Boot runs `~/.termux/boot/nanomuse.sh` at startup. The installer writes it unless
  `--no-boot` is passed.
- Termux:API must be installed for `termux-notification`. Without it the runtime still
  works; notifications simply stay in the web app.
- Android's battery optimization is the usual reason a reminder arrives late. Set Termux to
  *Unrestricted* in the system settings.

## Updating and removing

- Update the runtime: `nanomuse-phone update`. This pulls the source it installed, installs
  it into the same container and restarts. A copy installed with `--source` has no git
  history; run the installer again with the same `--source` to update it.
- Update the model: `nanomuse-phone model qwen3:4b` (or another name).
- Remove the runtime but keep the data: run `nanomuse-phone down`, then
  `proot-distro remove debian`. The data is in `~/.nanomuse`; the app and the model are not
  removed by that command.
- Remove everything: remove the Debian container, `~/.nanomuse`, and the `ollama` models
  (`ollama rm NAME`). Nothing was installed outside Termux and the app.

## Security

- The web app and the model listen on `127.0.0.1` only. Another device on the same Wi-Fi
  cannot reach them.
- The server keeps its access token in `~/.nanomuse/server_token`; use it as a bearer token
  if you call the API from a script on the phone.
- Termux's storage is private to the Termux app. The Debian container and the runtime live
  under it.
- The phone's model sees every message you send it. It is on your phone, under your
  control; nothing goes to a provider unless you add one.
