#!/usr/bin/env python3
"""Kindle Manager — local web app to view articles on your Kindle."""

import webbrowser
import threading
from flask import Flask, jsonify, render_template_string
import kindle_device

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kindle Manager</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #fafafa;
      color: #111;
    }

    header {
      padding: 2rem 2rem 1.5rem;
      border-bottom: 1px solid #e8e8e8;
      background: #fff;
    }

    header h1 {
      font-size: 1.2rem;
      font-weight: 600;
      color: #111;
    }

    header p {
      font-size: 0.85rem;
      color: #888;
      margin-top: 0.25rem;
    }

    main {
      max-width: 680px;
      margin: 0 auto;
      padding: 1.5rem 1rem;
    }

    .status {
      text-align: center;
      padding: 4rem 1rem;
      color: #888;
    }

    .status strong {
      display: block;
      font-size: 1.1rem;
      color: #555;
      margin-bottom: 0.5rem;
    }

    ul {
      list-style: none;
      background: #fff;
      border: 1px solid #e8e8e8;
      border-radius: 8px;
      overflow: hidden;
    }

    li {
      padding: 1rem 1.2rem;
      border-bottom: 1px solid #f0f0f0;
    }

    li:last-child { border-bottom: none; }

    .title {
      font-size: 0.95rem;
      line-height: 1.4;
    }

    .snippet {
      font-size: 0.8rem;
      color: #aaa;
      margin-top: 0.2rem;
      line-height: 1.4;
    }
    #count {
      font-size: 0.85rem;
      color: #888;
      margin-bottom: 1rem;
    }
  </style>
</head>
<body>
  <header>
    <h1>Kindle Manager</h1>
    <p>Plug in your Kindle, then refresh to see your articles.</p>
  </header>
  <main>
    <div id="root"><p class="status">Loading...</p></div>
  </main>

  <script>
    function emptyState() {
      return `<div class="status">
        <strong>No articles on your Kindle</strong>
        Send some articles and they will appear here.
      </div>`;
    }

    async function load() {
      const root = document.getElementById("root");
      const res = await fetch("/api/documents");
      const data = await res.json();

      if (!data.connected) {
        root.innerHTML = `
          <div class="status">
            <strong>Kindle not connected</strong>
            Plug in your Kindle via USB, then refresh this page.
          </div>`;
        return;
      }

      if (data.documents.length === 0) {
        root.innerHTML = emptyState();
        return;
      }

      root.innerHTML = `
        <p id="count">${data.documents.length} article${data.documents.length === 1 ? "" : "s"}</p>
        <ul id="list"></ul>`;

      const list = document.getElementById("list");

      for (const doc of data.documents) {
        const li = document.createElement("li");
        li.innerHTML = `
          <div class="title">${escapeHtml(doc.title)}</div>
          ${doc.snippet ? `<div class="snippet">${escapeHtml(doc.snippet)}</div>` : ""}`;
        list.appendChild(li);
      }
    }

    function escapeHtml(str) {
      return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    load();
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/documents")
def api_list():
    if not kindle_device.is_connected():
        return jsonify({"connected": False, "documents": []})

    docs = kindle_device.list_documents()
    return jsonify({
        "connected": True,
        "documents": [{"title": d.title, "filename": d.filename, "snippet": d.snippet} for d in docs],
    })
def open_browser():
    webbrowser.open("http://localhost:5001")


if __name__ == "__main__":
    threading.Timer(0.5, open_browser).start()
    print("Kindle Manager running at http://localhost:5001")
    print("Press Ctrl+C to stop.")
    app.run(port=5001)
