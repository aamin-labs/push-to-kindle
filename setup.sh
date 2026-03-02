#!/bin/bash
set -e

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "Created .env — open it and set KINDLE_EMAIL to your @kindle.com address."
else
    echo ".env already exists — skipping."
fi

echo ""
echo "Setup complete. To use:"
echo "  source .venv/bin/activate"
echo "  python3 send_to_kindle.py <url>"
echo "  python3 send_to_kindle.py --dry-run <url>   # preview without sending"
