---
name: gemini
description: Consult Google's Gemini model from the command line via the pre-installed `gemini` CLI. Use when the user explicitly asks to "ask Gemini", "check with Gemini", "use gemini-cli", get a second model's opinion, cross-check Claude's own reasoning, or when a task benefits from Gemini's very large context window (analysing big files or whole codebases in one pass) or Google Search grounding. SKIP when the user has not asked for Gemini and the task is squarely within Claude's own capability.
---

# Gemini CLI

`gemini` (Google's official `@google/gemini-cli`) is installed in Maude and on
PATH. Use it to get answers from Google's Gemini models.

## Step 0: confirm credentials BEFORE doing anything else

Gemini only works if working credentials are present. Do a cheap probe first:

```bash
printf 'reply with the single word: ok\n' > /tmp/gemini-probe-$$.txt
gemini < /tmp/gemini-probe-$$.txt; echo "exit=$?"
rm -f /tmp/gemini-probe-$$.txt
```

If that errors or reports it is not authenticated, STOP and tell the user to add a
key via the Maude TUI: **Set Creds → Paste exports**, pasting e.g.
`export GEMINI_API_KEY=...` (the Paste-exports tab recognises `GEMINI_*` and
`GOOGLE_*` variables and stores them so future sessions pick them up). Do NOT ask
for keys in chat, and do not retry blindly.

## CRITICAL: always use file-based handoff — never put the prompt on the command line

Passing prompt/code text as a shell argument causes quoting-and-escaping bugs
(backticks, quotes, `$`, and newlines all break the shell parser). You MUST hand
the prompt to Gemini through a file on **stdin** instead. Every single time:

1. **Pick a fresh, unique temp file path under `/tmp`** — NEVER inside the project
   or any git working tree. Choose a new name on each call, e.g.
   `/tmp/gemini-query-<random>.txt`.
2. **Write the full prompt and any context to that file using the Write tool.**
   Do not interpolate the prompt into a command string. To include code, paste the
   file contents into the query file as part of what you Write (or `cat` files into
   it). Do not pass file paths expecting Gemini to open them.
3. **Run Gemini by feeding the file on stdin** — the prompt never touches the shell
   parser:
   ```bash
   gemini < /tmp/gemini-query-<random>.txt
   ```
   For long answers, capture to a file and Read it:
   `gemini < /tmp/gemini-query-<random>.txt > /tmp/gemini-out-<random>.txt 2>&1`
   Optional: `-m gemini-2.5-pro` selects a model.
4. **Read the result** (from the Bash stdout, or from the output file with the Read
   tool).
5. **Delete the temp files** when done — always, even if Gemini errored:
   ```bash
   rm -f /tmp/gemini-query-<random>.txt /tmp/gemini-out-<random>.txt
   ```

### Rules (do not deviate)
- NEVER write `gemini -p "..."` with the prompt inline. NEVER `echo "..." | gemini`.
- NEVER create the temp file inside `~/Maude/Projects/...` or any git working tree —
  always under `/tmp`. These files must never be committed.
- ALWAYS use a fresh, unique temp filename per invocation; ALWAYS `rm -f` it after.

## Authentication details

Gemini reads credentials from the environment. Any one of these is enough:

- `GEMINI_API_KEY` — Google AI Studio key (simplest; get one at
  https://aistudio.google.com/apikey). On its own this is enough.
- Vertex AI / Google Cloud: `GOOGLE_GENAI_USE_VERTEXAI=true` plus
  `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`, and EITHER `GOOGLE_API_KEY`
  (express mode) OR `GOOGLE_APPLICATION_CREDENTIALS` (path to a service-account
  JSON) / `gcloud` Application Default Credentials. Note: `GOOGLE_API_KEY` alone,
  without `GOOGLE_GENAI_USE_VERTEXAI=true`, does nothing — but if you paste
  `GOOGLE_API_KEY`/`GOOGLE_CLOUD_PROJECT` via Set Creds, Maude turns on Vertex
  mode and defaults `GOOGLE_CLOUD_LOCATION` to `us-west1` (Oregon) for you.
- Gemini Code Assist / OAuth: `GOOGLE_GENAI_USE_GCA=true`.

There is no `GOOGLE_KEY` variable — that name is not recognised.

These are set through **Set Creds → Paste exports** in the Maude TUI, which stores
`GEMINI_*`/`GOOGLE_*` vars in `~/.gemini/.env` — the file the Gemini CLI auto-loads
on every run — so future sessions pick them up automatically.

## Example

Ask Gemini to review a large file (Write the query file, then):
```bash
gemini < /tmp/gemini-query-7f3a.txt > /tmp/gemini-out-7f3a.txt 2>&1
# Read /tmp/gemini-out-7f3a.txt, then:
rm -f /tmp/gemini-query-7f3a.txt /tmp/gemini-out-7f3a.txt
```
where `/tmp/gemini-query-7f3a.txt` was written (via the Write tool) as the review
instruction followed by the full file contents.
