# SkillRack Helper App

This folder contains a Flet-based desktop and Android-friendly client that mirrors the extension flow directly inside the app.

## What it does

- Uses the backend only for `/api/auth`, `/api/log`, and `/`.
- Performs SkillRack login, question loading, captcha handling, submit polling, and logout from the app itself.
- Keeps the same request payloads used by the extension flow.
- Supports copy-question and response viewing.

## Prerequisites

- Python 3.11 or newer.
- The backend from `blast.py` running locally or on a reachable host.
- Flet installed in the same environment you use to run the app.

## Backend setup

Run the backend that exposes only these routes:

- `GET /`
- `POST /api/auth`
- `POST /api/log`

Example:

```bash
python -m uvicorn blast:app --host 127.0.0.1 --port 5000 --reload
```

## Install

From the project root:

```bash
pip install -r app/requirements.txt
```

If you want the exact Flet release used for this app, install or verify `flet==0.85.3`.

## Run on desktop

From the project root:

```bash
python app/main.py
```

You can change the backend URL in the app before verifying auth.

## Test

Quick syntax check:

```bash
python -m py_compile app/__init__.py app/skillrack_app.py app/main.py
```

Manual flow to verify:

1. Test the backend root endpoint.
2. Enter the auth code.
3. Log in with SkillRack credentials.
4. Load Daily Test or Daily Challenge.
5. Solve the captcha.
6. Submit code and confirm the response panel updates.

## Android packaging

Once the desktop flow works, package the same Flet app using your installed Flet Android workflow.

## Notes

- The app performs all SkillRack requests directly, matching the extension payloads.
- The backend is only used for auth and profile logging.
