#!/usr/bin/env bash
# Home Mate HIMS launcher for Linux / macOS.
# Layout: backend/ (Python code), frontend/ (pages and styling), instance/ (your data, created on first start).
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  python3 -m venv venv
  . venv/bin/activate
  pip install --upgrade pip
  pip install -r backend/requirements.txt
else
  . venv/bin/activate
fi
cd backend
python run.py
