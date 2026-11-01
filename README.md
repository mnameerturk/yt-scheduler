# NMR YouTube Scheduler (server, PC test version)

Runs on your Windows PC while the YouTube API audit is pending. Later the same code moves to a VPS.

## First start

1. Install **Python 3.11 or newer** from python.org if you don't have it (tick "Add python.exe to PATH").
2. Unzip this folder somewhere simple, e.g. `D:\yt-scheduler-server`.
3. Copy the JSON you downloaded from Google Cloud into this folder and rename it to **`client_secret.json`**.
4. Double-click **`run_windows.bat`**.
   - First run installs everything (1–2 minutes) and asks for an app password. Write it down.
   - A second window opens with ngrok. Keep both windows open.
5. On your phone or PC, open **https://dayroom-cinch-scorer.ngrok-free.dev**
   - ngrok shows a "You are about to visit" page once: tap **Visit Site**.
   - Log in with the app password.
6. Tap **+ Add channel**, sign in with Gmail 1, pick the channel, tick every permission box.
   Google warns "Google hasn't verified this app": tap **Advanced → Go to NMR Scheduler**.
7. Tap **+ Add channel** again and sign in with Gmail 2 ("Use another account").

Both channels should now be listed. The first one is marked "Drive storage": all videos are kept in that Gmail's Drive until posted.

## Test with a short clip

Open a second PowerShell in this folder:

```
.venv\Scripts\activate
python send_to_queue.py --setup
python send_to_queue.py "C:\path\test.mp4" --channel "part of channel name" --title "Test upload" --time "2026-11-01 20:00"
```

Then watch the page: the video goes Scheduled → Posting → Posted, with a youtu.be link.
Use **Post now** in the page to skip waiting. While in test mode every upload is private (audit not approved yet).

## Daily use (until the phone app is ready)

- `python send_to_queue.py --status` lists the queue.
- Re-running the same send command after a dropped connection continues the upload.
- The PC (this server) must be on at posting time until we move to the VPS after the audit.

## Settings (`.env`)

| Key | Meaning |
| --- | --- |
| `BASE_URL` | public address (ngrok now, `https://nmrscheduler.duckdns.org` later) |
| `APP_PASSWORD` | password for the page, phone app and PC tool |
| `TIMEZONE` | times you type are in this zone |
| `TEST_MODE` | `1` uploads private; set `0` after the audit is approved |
| `DRIVE_MODE` | `shared` (one Drive) or `per_channel` (each Gmail's own Drive) |
