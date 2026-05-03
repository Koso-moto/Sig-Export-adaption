<!-- GitHub Copilot / AI agent guidance for the signal-export codebase -->

**Purpose**: Help an AI coding agent become productive quickly in this repo by describing the architecture, important files, developer workflows, and project-specific conventions.

**Quick orientation**
- **Package:** `sigexport/` — main runtime package. CLI entrypoint: `sigexport.main:cli` (see `pyproject.toml` [project.scripts]).
- **Top-level workflow:** `main.py` drives everything: it calls `data.fetch_data()` to read conversations and contacts, `create.create_chats()` to convert raw messages into `models.Message` objects, `files.copy_attachments()` to copy media, and `html.create_html()` / `html.prep_html()` to produce HTML output.
- **Primary data structures:** `sigexport.models` — `RawMessage`, `Contact`, `Message`, and helper predicates (`is_image`, `is_audio`, ...). Use these types when transforming data between modules.

**Key files to reference**
- `sigexport/main.py` — CLI commands, pagination, export loop, PDF generation (looks for Chrome binary).
- `sigexport/data.py` — (data fetch + DB handling) — where Signal DB is parsed (use to find DB access and decryption logic).
- `sigexport/create.py` — converts `RawMessage` → `Message` and constructs per-chat message lists.
- `sigexport/files.py` — attachment copying and naming conventions (attachments go in `media/` subfolder).
- `sigexport/html.py` — HTML assembly and `prep_media_pdf` logic.
- `sigexport/utils.py` — OS-specific Signal source locations (`source_location()`), timestamp helpers, and `fix_names()` (filesystem-safe chat names).
- `sigexport/models.py` — canonical message/attachment dataclasses and serialization helpers (`to_md()`, `dict_str()`).

**Project-specific conventions & patterns**
- CLI uses `Typer` — add commands as `@app.command()` functions in `main.py`.
- Dates and timestamps are in milliseconds; use `utils.dt_from_ts()` and be aware of the 64-bit timestamp shape (`high`/`low`).
- Chat folder naming: call `utils.fix_names()` before writing output — filenames must be filesystem-safe and deduplicated.
- Attachments in markdown: `models.Message.to_md()` expects images to be prefixed with `!` and links to use `./media/...` relative paths. `create.create_message()` constructs attachment paths using `Path('media') / fileName` and replaces whitespace with `%20`.
- Merging: `merge.merge_with_old()` handles combining an existing export with a new one — JSON output currently does not support merging (see warning in `main.py`).

**Build / dev / test workflows**
- Uses `rye` in `pyproject.toml` scripts. Common commands:
  - `rye fmt` — format
  - `rye lint` / `rye lint --fix` — linting
  - `rye run check` — typecheck (pyright)
  - `rye run test` — run tests (pytest)
  - `rye run sig` — run the CLI locally
- Tests are minimal (see `tests/test_utils.py`). Run the full suite with `rye run test`.

**Runtime & integration notes**
- Signal data: The code expects a Signal config directory containing `sql/db.sqlite` and `config.json`. Default OS locations are in `utils.source_location()` (macOS: `~/Library/Application Support/Signal`).
- Decryption: repo depends on `pycryptodome` and `sqlcipher3-wheels` — DB decryption may require passing `--password` or `--key` on Linux; check `data.py` for platform-specific steps.
- PDF generation relies on a local Chrome/Chromium binary; `main.generate_pdf()` searches common paths and PATH.

**When changing code, follow these concrete patterns**
- Preserve `models.Message` serialization shape: `to_md()` and `dict_str()` are used by downstream writers. Keep `date` serialized as ISO-8601 in `dict()`.
- If adding CLI flags, add them to `main.py` Typer commands and respect `logging.verbose` and existing exit patterns (`typer.Exit`).
- For new file outputs, follow existing structure: create `dest / chat_name` and write `chat.md`, `data.json`, and `{chat_name}.html` as shown in `main.py`.

**Examples (copy/paste patterns)**
- Create filesystem-safe name: `contacts = utils.fix_names(contacts)`
- Write messages to markdown and JSON:
  - `print(msg.to_md(), file=md_f)`
  - `print(msg.dict_str(), file=js_f)`
- Build attachments path in create: `path = Path('media') / file_name` then `path = Path(re.sub(r"\s", "%20", str(path)))`.

**Where to look for potential gotchas**
- Timezones: `parse_input_dt()` in `main.py` will inject local tz when missing — be careful when filtering ranges.
- Duplicate chat names: `utils.fix_names()` appends a numeric suffix to avoid collisions — don't bypass it.
- Platform-specific decryption steps and `config.json` presence checks are performed in `main.py` and `data.fetch_data()`; changes here affect all exports.

If anything here is unclear or you'd like more detail on any module (e.g. `data.py` DB-decryption flow, or `files.py` attachment renaming rules), tell me which area to expand and I'll iterate.
