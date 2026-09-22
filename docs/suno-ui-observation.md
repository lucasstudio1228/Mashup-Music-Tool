# Suno.com Create UI — Live Observation Report

**Purpose:** mandatory pre-implementation inspection of the real Suno create UI (spec STEP 0, section C).
This distinguishes **directly observed** facts (driven live with Playwright) from **docs/assumption**
and **unverified** items. Only the "Directly observed" section may be relied on for selectors.

- **When:** 2026-09-20
- **How:** Playwright (sync API) persistent context on the dedicated automation profile
  `.browser_profile_suno`, connected to `https://suno.com/create`. Scripts: `/tmp/suno_inspect*.py`
  (enumerate interactive controls, region text, and open menus **without** clicking Create/Download-to-file).
- **Account:** `lucas_studio1228` (user's own, logged in manually once — I did not enter credentials).
  Plan is Premier per user. **Credits remaining shown: 50** (button `aria-label="Credits remaining: 50"`).
- **Screenshots (in `data/`):** `suno_create_top.png` (Simple), `suno_advanced.png` (Advanced + More Options),
  `suno_models.png` (model picker), `suno_more.png` (⋯ menu), `suno_download_submenu.png` (Download formats).

> Method caveat: values below marked "observed" were read from the live DOM (roles, `aria-label`,
> `placeholder`, visible text). I did **not** click **Create** and did **not** trigger an actual file
> download during inspection (would consume credits / write files), so anything about *what a generation
> returns* or *the downloaded WAV itself* is explicitly marked **UNVERIFIED**.

---

## 1. DIRECTLY OBSERVED (live DOM)

### 1.1 Page & navigation
- Create page URL: `https://suno.com/create` (title "Suno | AI Music").
- Left app sidebar: Home, Explore, **Create**, Studio, Library, `<username>`, Earn Credits, Labs, Notifications, More.
- Credits indicator: top of create column, `button[aria-label="Credits remaining: 50"]` (text "50").

### 1.2 Creation modes — a 3-way tab group (NOT "Custom")
Three `role="tab"` buttons at the top of the create panel:
- **Simple** — single prompt textarea (placeholder-style example text e.g. "Chill mumble rap song…"),
  plus Audio / Voice / Image add-buttons, an "Add" button, "Clear all form inputs", and **Create**.
- **Advanced** — the full-control mode the spec calls "Custom/Advanced". Details in §1.4.
- **Sounds** — separate sound-generation mode ("Describe the sound you want"). Not used by this feature.

> Spec said "chọn chế độ tạo (Custom/Advanced)". The live label is **"Advanced"** (there is no tab named
> "Custom"). Use `get_by_role("tab", name="Advanced")`.

### 1.3 Model selector — observed models
- A button at the top-right of the panel shows the **current model version** (observed default: **`v6`**),
  ~`x=617,y=14`. Clicking it opens a popover listing:
  - **ALL MODELS:**
    - `v6` — "Pro · Powerful. Versatile. Refined. Our best model yet." (current default)
    - `v6-wild` — "Pro · Best for experimental ideas."
    - `v6-mini` — "A free, more efficient version of premium v6 models."
  - **MY MODELS** (user's custom-trained models): `Bamboo Flute v1`, `Handpan v2`, `Handpan v1`
    (each with a Delete button). Banner: "We automatically upgraded your custom models for v6."
  - **Create Custom Model** (Beta) — "Create a model based on your uploads (100 Credits)".
- **Config `preferred_model=v6` maps to the `v6` entry under ALL MODELS.** `allow_model_fallback=false`
  means: if the button does not already read `v6` and the `v6` option can't be selected, stop — do not
  silently accept another version.

### 1.4 Advanced panel — fields (top → bottom)
1. **Add sources:** `Audio` (aria "Add audio - Browse, upload, or record audio"), `Voice` (aria "Add Voice"),
   `Inspo` (aria "Add inspiration from a playlist"). Not used for text-to-instrumental.
2. **Song Title** — `input[placeholder="Song Title (Optional)"]`.
3. **Lyrics** — `div[role=textbox][aria-label="Lyrics editor"]`, **5000-char limit**
   (counter "0 of 5000 characters used"). Placeholder line:
   **"Start writing lyrics, or leave this empty for instrumental."**
   Cowriter tools: "Lyricist", "Ask Suno to write lyrics", Undo/Redo/New draft/Saved lyrics/Full-screen.
4. **Styles** — `textarea`, **1000-char limit** (counter "0/1000"),
   placeholder example "city pop, epic build-up, 1940s big band, experimental hip-hop, dynamic melodies".
   Accessories: "No saved styles", "Personalize style prompt to match your taste",
   "Refresh recommended styles", and clickable suggestion chips (`aria-label="Add style: <name>"`).
5. **More Options** — a collapsible (`div[role=button]` text "More Options"). Expanded, it reveals:
   - **Exclude styles** — `input[placeholder="Exclude styles"]`.
   - **Vocal Gender** — segmented buttons **Male / Female**.
   - **Duration** — segmented buttons **Custom / Auto**; choosing **Custom** shows a
     `input[type=number][placeholder="Auto"]` (enter target seconds).
   - **Max Mode** — segmented buttons **Off / On** (maps to config `max_mode`, default Off).
   - **Weirdness** — a slider control (observed reading "Expected results" / **50%**).
6. **Create** — `button[aria-label="Create song"]` (text "Create"); also "Clear all form inputs".

### 1.5 Workspaces = Suno's project grouping
- Left of the clip list: **"My Workspace" (3065 songs)** card (`div[role=button]`),
  **"Create new workspace"**, **"Search workspaces"** input, **"Archived"**, and a **"Workspaces"** button.
- This is the natural place to isolate one app-project's 15 songs (create/select a workspace before generating).

### 1.6 Generated-song list (right column) & per-song actions
- Each song row: Play (`div[role=button][aria-label="Play <title>"]` showing duration e.g. "3:40"),
  title link, **Edit title**, **Like**, **Dislike**, **Share**, **Publish**, **Remix**, **More options (⋯)**.
- List controls: **Filters** (default 3: `hideDisliked, hideStems, hideClipsFromEditMode`),
  Sort **New/newest**, View **List**, quick filters **Liked / Public / Uploads**, pagination
  (Previous / current page input / Next).
- **⋯ "More options" menu** items (observed): **Remix, Edit, Share, Download, Manage,
  Add to Queue, Add to Playlist, Song Radio, Report, Move to Trash.**

### 1.7 Download — formats confirmed
- ⋯ → **Download** opens a **dialog** titled "Download '<song title>'" offering formats:
  **M4A, MP3, WAV, MP4 video asset, Stems & MIDI.**
- Dialog note: "You've unlocked this song. You can return and download at any time. Learn more."
  → the song must be **unlocked** before WAV is downloadable (Premier appears to auto-unlock; verify per song).
- **WAV is a first-class official format here** — satisfies the hard rule "input must be a WAV downloaded
  from Suno" (no MP3→WAV faking needed).

---

## 2. IMPORTANT DISCREPANCIES vs. the spec (must drive the code)

1. **No "Instrumental" toggle exists.** The spec says "Bật Instrumental". In the live UI, instrumental is
   produced by **leaving the Lyrics field empty** (placeholder literally says so). Implementation: in
   Advanced mode, assert Lyrics is empty and never fill it → that *is* "instrumental=true". Do **not**
   hunt for / require a non-existent switch. (The old `bass`/`drums` allow-flags in the config have **no
   corresponding UI control** here — there is no per-stem inclusion toggle at generation time; only the
   Styles/Exclude-styles text can bias arrangement. Treat `allow_bass/allow_drums=false` as *style-prompt
   guidance* — put e.g. "no drums, no bass" into **Exclude styles** — not as a UI switch.)
2. **"Variety" vs "Weirdness".** The spec warned not to treat Variety as a rename of Weirdness. The live UI
   exposes **Weirdness** (a %) and **Max Mode**; **there is no control literally named "Variety".** Per
   config (`variety=0` "only if a control exists"), since no "Variety" control exists, **do not set it** and
   do not repurpose Weirdness for it. Leave Weirdness at its default unless the user explicitly asks.
3. **Model naming.** Config `preferred_model=v6`; live current default is `v6`. `v6-mini` is the free tier.
   With `allow_model_fallback=false`, require the selector to read `v6` (Pro).
4. **Mode name.** Config/flow says "Custom/Advanced"; the real tab is **"Advanced"**.

---

## 3. UNVERIFIED (must confirm during a budgeted live run — needs credits / clicking Create)

- **How many songs one Create returns** (spec `generation_strategy=paired_outputs` assumes 2). NOT tested
  (would consume credits). Plan: on the *first* real generation, snapshot the song-ID set before/after one
  Create and count new IDs; derive the number of Create actions for 15 (e.g. 8×2 → 16, keep 15, 1 spare).
- **Credit cost per generation** and whether v6 + Max Mode changes it (50 credits currently available).
- **WAV download mechanics:** whether selecting "WAV" starts an immediate browser download or first renders
  ("You can return and download at any time" hints WAV may need a render/unlock step). Must register a
  Playwright download-wait **before** clicking WAV and confirm one file per song.
- **Simple-mode instrumental behavior** (only Advanced was analyzed for the empty-lyrics rule).
- **Exact Weirdness/other slider DOM** (role/aria of the range input) — the label+value were read, but the
  precise settable element wasn't manipulated.
- Whether a per-account/workspace **download allowance** limit is surfaced anywhere (config
  `max_new_song_downloads=15` is our own cap regardless).

---

## 4. Selector seeds for `SUNO_SELECTORS` (from observed DOM; keep overridable)

| Purpose | Primary selector (observed) |
|---|---|
| Advanced mode tab | `role=tab[name="Advanced"]` |
| Simple mode tab | `role=tab[name="Simple"]` |
| Model version button | top-right panel button whose text is the version (e.g. `v6`) |
| Model option (v6) | menu item text `v6` under "ALL MODELS" |
| Song title | `input[placeholder="Song Title (Optional)"]` |
| Lyrics editor | `[role=textbox][aria-label="Lyrics editor"]` (leave empty ⇒ instrumental) |
| Styles box | `textarea` under the "Styles" label (placeholder "city pop, epic build-up, …") |
| More Options expander | text "More Options" |
| Exclude styles | `input[placeholder="Exclude styles"]` |
| Vocal Gender | buttons "Male" / "Female" |
| Duration mode | buttons "Custom" / "Auto"; custom seconds `input[type=number][placeholder="Auto"]` |
| Max Mode | buttons "Off" / "On" under "Max Mode" |
| Create | `button[aria-label="Create song"]` |
| Clear form | `button[aria-label="Clear all form inputs"]` |
| Credits | `button[aria-label^="Credits remaining"]` |
| Workspace list card | `div[role=button]` containing the workspace name |
| New workspace | text "Create new workspace" |
| Song row play | `div[role=button][aria-label^="Play "]` |
| Song ⋯ menu | `button[aria-label="More options"]` |
| Download entry | `button[aria-label="Download"]` (opens format dialog) |
| WAV format | dialog button text "WAV" |

> All selectors are **best-effort from one observation** and Suno's UI changes often. They must live in a
> single overridable table (`SUNO_SELECTORS` + `suno_overrides.json`), and every driver step must fail with
> a clear `UI_CHANGED` message when a selector is missing — never silently continue.
