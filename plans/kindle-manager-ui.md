# Plan: Kindle Manager UI

> Source PRD: GitHub issue — Kindle Manager UI (local web app to list and delete articles over USB)

## Architectural decisions

- **Device mount point**: `/Volumes/Kindle` (USB, same as `sync_highlights.py`)
- **Document directory**: `/Volumes/Kindle/documents/`
- **Excluded system files**: `My Clippings.txt`, `My Vocabulary Builder.sdr`, `.sdr` sidecar directories, hidden files
- **Included file types**: `.azw3`, `.mobi`, `.epub`, `.html`, `.pdf`
- **Deletion**: remove document file + matching `.sdr` sidecar directory
- **Routes**:
  - `GET /` — serves the article list page
  - `GET /api/documents` — returns JSON list of documents on device
  - `DELETE /api/documents/<filename>` — deletes a document from Kindle
- **Key models**: `Document(title: str, filename: str)`
- **Framework**: Flask, localhost only, no auth, single user
- **No calibre required** — direct filesystem access via Python `pathlib`

---

## Phase 1: List Articles

**User stories**:
- As a reader, I want to see all articles currently on my Kindle when I plug it in
- As a reader, I want a clear message when my Kindle is not connected
- As a reader, I want only articles in the list (not system files or clippings)
- As a reader, I want article titles to be human-readable (not raw filenames)

### What to build

A thin end-to-end slice: Kindle filesystem → API → browser.

Build a `kindle_device` module with `is_connected()` and `list_documents()`. Wire it to a `GET /api/documents` endpoint that returns a JSON array of documents. The homepage (`GET /`) renders either the article list or a "Kindle not connected — plug in your Kindle and refresh" message. Titles are derived from filenames (strip extension, un-sanitize underscores). Include a launch command that starts the Flask server.

### Acceptance criteria

- [x] Plugging in the Kindle and visiting `localhost:5001` shows a list of article titles
- [x] Unplugging the Kindle (or visiting without it connected) shows a "not connected" message
- [x] System files (`My Clippings.txt`, `.sdr` folders, hidden files) do not appear in the list
- [x] Each list item shows a human-readable title derived from the filename
- [x] `python3 kindle_manager.py` starts the server without error

---

## Phase 2: Delete Articles

**User stories**:
- As a reader, I want to delete a single article with one click
- As a reader, I want a confirmation prompt before deleting
- As a reader, I want the article to disappear from the list immediately after deletion

### What to build

Add a delete button to each article row. Clicking it shows a browser confirmation prompt. On confirm, the browser sends `DELETE /api/documents/<filename>` to Flask. The server removes the file and its `.sdr` sidecar directory from `/Volumes/Kindle/documents/`. The article row disappears from the UI without a full page reload.

### Acceptance criteria

- [x] Each article has a delete button
- [x] Clicking delete shows a confirmation dialog before proceeding
- [x] Confirming removes the `.azw3` (or other format) file from the Kindle
- [x] The matching `.sdr` sidecar directory is also removed if it exists
- [x] The article row disappears from the UI immediately after deletion (no full page reload)
- [x] Cancelling the confirmation does nothing

---

## Phase 3: Launch UX + Polish

**User stories**:
- As a reader, I want the app to start with a single Terminal command
- As a reader, I want the browser to open automatically when I start the app
- As a reader, I want an Instapaper-style design that's easy to scan
- As a reader, I want a clear empty state when all articles have been deleted

### What to build

Add `webbrowser.open()` so the browser launches automatically on server start. Style the page to resemble Instapaper: clean white background, article titles in a readable font, subtle delete buttons. Add an empty state message ("No articles on your Kindle") when `list_documents()` returns an empty list. Add basic error handling for a Kindle that disconnects mid-session (return a clear error from the API, show a message in the UI).

### Acceptance criteria

- [x] Running `python3 kindle_manager.py` opens the browser automatically at `localhost:5001`
- [x] The page is clean and easy to scan (Instapaper-style)
- [x] An empty state message appears when there are no articles on the device
- [x] If the Kindle is disconnected mid-session, the UI shows an error rather than crashing
- [x] The app can be stopped with `Ctrl+C`
