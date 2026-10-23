# Jarvis

A voice butler for this PC, in the style of Iron Man's J.A.R.V.I.S.: a deep, polite British voice that calls you
"sir". One of two **brains** does the everyday work itself (apps, bookmarks, files, notes and small scripts, volume,
music, reminders, questions) and only hands genuinely complex work to **Claude Code** in a terminal or
**Claude in Chrome**, then tells you how it went. The brains: **Gemini Live** (cloud: fast, best quality) and
**Qwen 3.8 27B** (runs on this PC: private, works offline, slower). Switch between them any time.

## Run

```bash
./run.sh            # or open "Jarvis" from the app grid
./run.sh --text     # no mic/speaker, type in the window
./run.sh --devices  # list audio devices (for MIC_DEVICE / SPEAKER_DEVICE in .env)
./run.sh --doctor   # checks key, audio, Chrome extension, Qwen, OpenCode, memory - and says how to fix each
tests/run_all.sh    # all tests; safe while Jarvis runs (temp memory file, isolated Chrome on port 8799)
```

Put your key in `.env` (`GEMINI_API_KEY=...`, see `.env.example` for every setting). Closing the window quits.
Launching again while it runs just brings the window back; it never runs twice.

## What it does

| You say | Who does it |
|---|---|
| "open my LeetCode bookmark", "open a terminal", "what time is it" | Jarvis, instantly |
| "write a script that…", "make a note…", "volume down", "pause the music", "remind me in 20 minutes" | Jarvis, instantly |
| "install htop", "maximize Chrome" (needs the Window Calls extension) | Jarvis, after you approve the plan |
| "add a settings page to my jarvis project", "build me a Flask app…" | Claude Code in a terminal (complex work only) |
| "star the anthropic SDK repo on GitHub", "fill in this form" | Claude in Chrome |
| "explain how attention works in transformers" | a stronger Gemini text model |
| "what's on my screen?", "read that error", "where is the submit button?" | Jarvis looks at a screenshot; for "where…" his glowing cursor glides to it and highlights it |
| "what's my morning plan?", "run my night log", "how was my week?" | Your three scheduled Claude tasks, run on demand (see **Routines**) |
| "research X in depth" | Claude reads many sources for a few minutes while Jarvis talks you through it |

- **Plans**: multi-step requests get a checklist in the window, done one step at a time and ticked off live
  (Claude reports each step as it finishes it).
- **Approval**: anything that installs, deletes or changes the system shows the plan first and waits for
  "yes" (or the Approve button). Packages install through `pkexec`, so GNOME asks for your password.
- **Claude terminals** show readable progress, close themselves 5 s after finishing (failures stay open),
  and Jarvis notices if you close one early. Each run is kept in `tasks/<id>/` for a week.

## Two brains: Gemini and Qwen

| | Gemini (cloud) | Qwen (this PC) |
|---|---|---|
| Hears you with | Gemini Live | Whisper large-v3-turbo on the GPU |
| Thinks with | Gemini | `qwen3.8:27b` via Ollama |
| Speaks with | Gemini's voice (Algieba) | Kokoro, a deep British voice |
| Needs | `GEMINI_API_KEY`, internet | `scripts/install_local.sh` (about 20 GB), no internet |
| Speed | replies in ~1-2 s | instant commands ("open my YouTube", volume, time, "what's left") are answered by code; other replies are slower (see **Good to know**) |

**How fast is Qwen here?** Measured on this PC (RTX 4060 with 8 GB, 31 GB RAM): `qwen3.8:27b` is a 17 GB model, so
about two thirds of it runs on the CPU, which limits it to roughly **3-4 words per second** (the model's built-in
multi-token prediction is already on). So Jarvis works around it:
- **Instant commands never touch the model**: open a bookmark/site/app, search, volume, media, time/date, reminders,
  "what's left", "I finished X", the three routines, "what's on my screen", "where is the … icon": **~0.5 s**.
- Simple tool results are confirmed by code ("Opening YouTube, sir."), skipping a second model pass.
- Anything else goes to the model: a short answer starts speaking after **~7 s**; a request that needs a tool
  (and a spoken result) takes **~10-13 s**. Each reply is capped short; sentences are spoken as they're written.
- Gemini answers in 1-2 s, so use Qwen when you want **private/offline**, Gemini when you want **fast**.
- Taking over from Gemini takes ~30 s the first time (loading Whisper and the model, while Gemini keeps talking),
  then ~0.2 s while Qwen is still warm.
- `LOCAL_GPU_LAYERS` (default auto) decides how much of the model sits on the GPU; Whisper shares that GPU.

**Switching**: click the **Gemini | Qwen** toggle in the window (or press **Alt+B**), or just say *"switch to Qwen"* /
*"switch to Gemini"* (also "go back to Gemini", "use the local one"). The other brain **warms up first while the
current one keeps talking**, then takes over by itself with the conversation carried across (so there's no dead air
and it doesn't forget what you were doing), and says a short "Qwen here, sir." Everything running keeps running:
Claude jobs, plans, today's memory, the cursor. The choice is remembered for the next start (`BRAIN=` pins one).
After a switch away, Qwen stays in memory for `LOCAL_KEEP_WARM_MIN` minutes so switching back is quick, then the GPU
is freed. Screen vision, pointing, `ask_gemini` and research still use your Gemini key from either brain.

## Calls on your behalf (WhatsApp Web, Google Meet)

Ask: *"call Rahul on WhatsApp and tell him I'll be 15 minutes late, and ask whether to bring the report"* or *"join this Meet
(abc-defg-hij) and say I'm running 5 minutes behind"*. Jarvis reads back who, where and what it will say, waits for your
**yes**, then opens the chat, places the call, waits until it is answered, and talks to them in **its own voice** (a second
Gemini Live session just for the call), keeping to the mission you gave it.

- **You hear everything live**: the caller *and* Jarvis, about 80 ms delayed, through your normal speakers/headphones. The
  window shows the running transcript. With headphones you can keep talking to Jarvis normally during the call
  (*"tell her 5 pm"* is passed into the call). On speakers Jarvis ignores its microphone while call audio is playing, so it
  can't mistake the caller for you; headphones are better.
- **Take over any time**: *"I'll talk"* (Jarvis goes silent and your real microphone goes into the call), *"carry on"* (Jarvis
  resumes), *"hang up"*.
- **It says what it is**: it opens with *"Hello, this is Jarvis, <you>'s AI assistant... I'm taking notes"* (`CALL_DISCLOSE=off`
  shortens the greeting, but it always answers honestly if asked), never imitates your voice, never agrees to money or
  commitments, never gives out passwords/codes/card or ID details, hands anything unexpected back to you, and stops after
  `CALL_MAX_MINUTES` (10). Note that the other person's audio is processed by Gemini and kept as a text transcript in memory.
- **How the audio works**: two virtual devices (PipeWire). The call tab's microphone is Jarvis' voice, the call tab's sound
  goes to Jarvis, and both are looped to your speakers. Only audio Chrome opens *after* the call starts is touched (your music
  or another meeting is left alone); everything is put back and removed when the call ends. First call: Chrome asks to allow
  the microphone for web.whatsapp.com / meet.google.com: click Allow once.
- **Already in a call? Say "take over"**: Jarvis finds the call running in Chrome (Meet or WhatsApp), mutes your other sounding tabs
  for the duration, moves the call's audio onto its virtual devices and carries on from there ("Hello everyone, this is Jarvis,
  <you>'s AI assistant. <you> asked me to take over..."). **"Join the meeting link in the group"** finds the Meet link in the open
  chat; if that meeting is already open in a tab it reuses it instead of opening a second copy. **A group call running in a WhatsApp
  chat** ("Ongoing call"): Jarvis presses Join and takes part. Ask "is there a call going on?" any time.
- **Out-of-date extension**: Chrome only picks up extension changes when you press Reload on `chrome://extensions`. Jarvis now compares
  versions when the extension connects, tells you in the window, and every call error says so plainly (the old behaviour was a silent
  failure). `./run.sh --doctor` shows it too.
- Answer an incoming WhatsApp call for you: *"answer the call and tell them I'll call back in an hour"*.
- Caveat: WhatsApp Web's call screen changes often; the controls are matched by their labels and tested on mock pages, so the first
  real call may need a small selector fix (`./run.sh --doctor`, and the buttons it can see are shown in the log).

## Qwen: fast voice model + big coding model

The voice brain runs **`qwen3:8b`**, which fits entirely on the 8 GB GPU: about 40 tokens/s, a tool call in under a
second (the 27B managed 2-3 tokens/s because two thirds of it ran on the CPU). Memory, to-do and quick Chrome commands
are answered by code with no model at all. The 27B (`qwen3.8-oc`) stays for **OpenCode coding jobs**, where quality
matters more than speed. Qwen only sees a compact 24-tool list (~2,000 tokens), because its context window is small.
Set `LOCAL_MODEL=` to change the voice model. Compare models: `.venv/bin/python tests/bench_local.py MODEL [gpu_layers]`.

## Qwen runs only when needed

Ollama is no longer a background service (disabled at login). It starts when you switch to Qwen or give OpenCode a
job, and **unloads the model and stops again** 2 minutes after you switch back to Gemini (`LOCAL_KEEP_WARM_MIN`),
90 seconds after an OpenCode job ends, and when Jarvis quits. `STOP_OLLAMA_WHEN_IDLE=off` keeps the server up (the
model still unloads). To start it at login again: `systemctl --user enable ollama`.

## OpenCode on Qwen, and Chrome control

**OpenCode (coding by voice)**: say *"use opencode to add a login page to my flask app"* (or just ask for coding work).
Jarvis opens OpenCode in a terminal on the local Qwen model (`OPENCODE_MODEL`, default `qwen3.8-oc` = the same
`qwen3.8:27b` with a 16k context, made by `ollama create qwen3.8-oc`; OpenCode needs far more than Ollama's 4k default,
without it the model never sees the task. Expect minutes per job on this GPU, not seconds; registered in
`~/.config/opencode/opencode.json`), narrates what it is doing every few seconds, and reports the result.
Say "open opencode live" for the interactive OpenCode screen if you want to steer it yourself.

**Chrome control (Jarvis Hands extension)**: a Manifest V3 extension in `chrome_extension/`, built the way Claude in
Chrome works: a connection to this app, page reading with numbered elements, and real (debugger) clicks and typing.
1. Open `chrome://extensions`, switch on **Developer mode**, **Load unpacked**, pick the `chrome_extension` folder.
2. Start Jarvis. The extension icon shows **ON** when connected (only that extension can connect: the socket checks its fixed ID).
3. Say *"new tab"*, *"close this tab"*, *"close the github tab"*, *"switch to youtube tab"*, *"go back"*, *"scroll down"*,
   *"click sign in"*, *"type hello in the search box and press enter"*: answered instantly by code. Longer web tasks
   are done by the brain step by step: read, click, type, read again.
While the debugger is attached Chrome shows a "started debugging this browser" bar; it goes away seconds after the last action.

### Memory, to-dos and what is on your screen

- **Self-managing memory**: conversation older than 45 days is folded into one short searchable digest per day (kept
  forever); only if the file outgrows a tenth of your free disk (at least 2 GB) are the oldest raw turns dropped
  (`MEMORY_MAX_MB=<n>` sets a hard cap instead). Every 30 minutes Gemini reads the new conversation and saves the
  lasting facts and to-dos by itself (never passwords or card numbers). To-dos take due dates ("by friday",
  "tomorrow") and can be listed as today / overdue / this week. `python memory.py stats|search WORDS|todos|tidy|export FILE`.
- **Long-term memory**: `~/.local/share/jarvis/memory.db` (SQLite, **no fixed cap**: it grows as needed and tidies itself). It keeps facts you tell it ("remember that…"), your **to-do list** ("add X to my
  to-do", "what's on my list", "I did X", also whatever Jarvis finds in Notion), the **full result of every research /
  Claude / OpenCode job** (searchable; ask "what did you find?" and he reads all of it), the conversation, and what you
  were last doing. Both brains load it at start, so it survives restarts. Tools: `remember`, `recall`, `todo`, `recall_research`.
- **Finds things by meaning**: "when is my sister's birthday" finds "Priya's birthday is 14 November" (Gemini
  embeddings stored in the same file; plain word search when offline). Unknown things come back as unknown, not guesses.
- **Knows what you're looking at**: the extension pushes every tab change (and which WhatsApp chat is open, checked
  every 3 s) to Gemini as a silent note, so it acts at once instead of asking Chrome first.
- **Knows what you're looking at (details)**: the extension reports your active tab, so with WhatsApp on screen "open Aryan's
  chat" opens that chat in WhatsApp (never a file search). "Open WhatsApp" switches to the WhatsApp tab that is already
  open; only "another/new one" opens a second. Typing goes to the named box (message vs search), and WhatsApp
  messages are sent with `wa_chat` + `wa_send`.
- **Jarvis Hands v3 can**: reuse tabs by app name ("open WhatsApp", "gmail", "youtube"); read pages and see your
  highlighted text; click (by text, "the second Reply", inside a named section), hover (CSS menus too), drag and drop,
  tick boxes, pick dropdown options, **fill a whole form** in one go; control video/audio (play, pause, skip 10 s,
  speed, fullscreen); pin/mute/duplicate/reopen tabs, zoom, group tabs, search history and bookmarks, bookmark a page;
  wait for slow pages and late-appearing buttons. It sees inside iframes and shadow DOM, notices when a click opens a
  new tab, and if a page ignores a normal click it clicks with a real mouse (the debugger bar then shows for a moment).
  A brief green ring shows what it is acting on.
- **No more "started debugging" bar**: clicks and typing use ordinary page events; the debugger is only a last resort.
- After updating the extension: `chrome://extensions` -> reload **Jarvis Hands**, then restart Jarvis.

## Routines and today's memory

Your three scheduled Claude tasks (they live on your Claude account; their instructions are copied word for word
into `routines/*.md`) run on demand, headless, with Claude reading your Notion plan and your Night Log:

| Say | Runs | Then |
|---|---|---|
| "what's my morning plan?" | **Morning Plan** | Jarvis tells you yesterday, today's tasks and the focus, and saves the tasks in **today's memory** |
| "run my night log" | **Night Log check-in** | Claude's questions come back; Jarvis asks you them conversationally, then sends all your answers into the *same* Claude conversation; Claude reviews, drafts the X post and saves the entry; Jarvis then **closes today's memory** |
| "weekly review" | **Weekly Roadmap Review** | numbers, verdict and next week; the X thread is on screen (say "copy it") |

- **Today's memory** (`~/.local/state/jarvis/day.json`) lives from the morning plan until the night log: not per
  session, not per 24 h. Restart Jarvis as often as you like and he still knows. Tell him what you did ("I
  finished the DSA set") and he ticks it off; ask "what's left?" any time. Things done that weren't on the plan are
  remembered too. The night log is Claude's to remember, so closing the day just deletes this file. A new morning
  plan replaces whatever was left.
- **Conversations continue**: answers and follow-ups go back into the same Claude session (`claude --resume`),
  also after a restart ("continue my night log").
- **After midnight** the night log is for the day that just ended; in the evening it's for today (or say
  "for yesterday").
- **Editing a task in Claude?** Update the matching file in `routines/` (the body is the task's prompt). Jarvis adds
  a short voice adapter on top, so don't put the adapter in the file.
- `ROUTINES_TERMINAL=on` shows a terminal while a routine runs; `JARVIS_ROUTINES_DRY_RUN=1` is a test switch that
  stops the night log from saving anything.

## Seeing, pointing, narrating

- **Screen**: only when you ask. Jarvis takes one screenshot (GNOME's own screenshot portal, so you may see the
  usual flash), sends it to Gemini vision, and deletes the file straight away. Nothing is stored. Don't ask him to
  look while a password or private message is open.
- **Cursor**: a transparent, click-through overlay (an XWayland window), so your own mouse and clicks pass
  straight through it. It only points; it never moves or clicks your real mouse. Say "hide the cursor", or press
  Esc, to dismiss it early.
- **Narration**: during long Claude jobs and research Jarvis says what is happening ("I'm reading python.org…")
  about every 15 s while nobody is talking, and says "still on it" if a step takes long. The same steps show in
  the window. Claude is asked to announce each major step in a sentence, which is what you hear.

## Window

The orb shows what Jarvis is doing: blue = ready, cyan = listening, purple = thinking, pink = speaking,
amber = working, green = waiting for your OK. Click the orb or press **Esc** to stop him talking,
**Ctrl+M** to mute the mic, or type in the box.

## Good to know

- **Headphones**: Jarvis follows your system's output. With headphones/a headset connected (Bluetooth, USB or
  3.5 mm) he speaks through them *and* listens through their own mic; with nothing connected he uses your default
  speaker and the PC's mic. Connect or disconnect headphones any time: he switches within ~3 s and the window
  shows what is in use (🎧 / 🔈). A Bluetooth headset switches to phone-call sound quality while its mic is open,
  so music playing at the same time sounds muffled; set `PREFER_HEADSET_MIC=off` to listen with the PC mic and
  only *play* through the headphones.
- **Latency**: Bluetooth headsets switch to a low-quality, high-latency "call" mode when their mic is in
  use. For the snappiest replies use a wired/USB mic or the laptop mic (`MIC_DEVICE`) with any output.
- **Echo**: on speakers, the mic is muted while Jarvis talks (so he doesn't hear himself); interrupt with
  Esc or a click. With headphones you can simply talk over him. Force with `ECHO_GUARD=on|off`.
- **Access**: delegated Claude runs use `CLAUDE_PERMISSION_MODE` (default `bypassPermissions`, i.e. full
  access). Truly destructive shell commands (`rm -rf /`, `mkfs`, …) are always refused.
- **Mishearing**: Jarvis asks "Pardon me, sir?" instead of guessing. Speaking a little closer to the mic helps
  most; if he cuts you off mid-sentence, raise `VAD_SILENCE_MS` (e.g. 900).
- **Voice**: `GEMINI_VOICE` in `.env`. Deep options: Algieba (default, smooth), Charon, Alnilam, Gacrux, Puck.
- **Jarvis doesn't hear me**: run `.venv/bin/python agent.py --mic-test`, speak, and check the level meter. Jarvis picks a real microphone by itself (the system default can be a Bluetooth speaker's
  loopback, which hears music, not you); the choice is logged as `microphone: …`. To force one, set `MIC_DEVICE`
  in `.env` (list them with `--devices`). A Bluetooth headset mic only works after switching the headset to call
  mode (Jarvis does this itself unless `PREFER_HEADSET_MIC=off`).
- **Logs**: `~/.local/state/jarvis/jarvis.log`.

## Code map

| File | Role |
|---|---|
| `agent.py` | entry point, single-instance lock, logging |
| `base.py` | the shared assistant: mic gate, window state, tool/plan/job plumbing, brain switching |
| `jarvis.py` | assembles the assistant and its two brains |
| `brain_gemini.py` | the Gemini Live brain (resumable, auto-reconnect), turn handling, tool calls |
| `brain_local.py`, `fastpath.py`, `stt.py`, `tts.py` | the Qwen brain: Whisper → Ollama (Qwen, tools) → Kokoro, plus instant command patterns |
| `scripts/install_local.sh` | installs Ollama (user level, no sudo) and the Qwen model |
| `tools.py` | tool declarations, duplicate guard, approval gate, plan tracking |
| `jobs.py` | background jobs: Claude runs, installs, GNOME extensions, Gemini text models |
| `routines.py`, `daymem.py`, `routines/` | your three scheduled tasks, their voice adapter, and today's memory |
| `delegate.py`, `claude_view.py` | launching Claude in a terminal and its progress view |
| `desktop.py` | local actions: apps, bookmarks, files, URLs, windows, shell |
| `audio.py` | mic (high-pass filtered) and interruptible low-latency speaker |
| `prompt.py` | the voice model's instructions |
| `screen.py`, `helpers/screenshot_portal.py` | screenshot via the desktop portal, Gemini vision (describe / locate) |
| `pointer.py` | the on-screen cursor overlay |
| `llm.py` | hedged Gemini calls (best model first, next one if it is slow or busy) |
| `ui.py`, `ui/index.html` | the animated window (local websocket, per-run token) |
