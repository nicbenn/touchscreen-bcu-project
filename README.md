# Touchscreen BCU Project

Functional replica of an Adelaide Metro **Bus Control Unit** for Raspberry Pi 3B+ and later Pi boards. Built from your photos: start-shift idle screen, numeric keypad, initialization, brightness/volume, and messages.

## Run on this PC

```powershell
cd C:\Users\MrNic\Touchscreen-BCU-Project
py -3 -m pip install -r requirements.txt
py -3 run.py
```

Or double-click `run.bat` after Python 3.12 is installed.

Open http://127.0.0.1:8080 — a splash appears, then **Start shift**.

Default test login (edit `config.yaml`):

- Shift number: any digits (e.g. `6440`)
- Badge: `12345`
- PIN: `0000`

## Splash before an update

Replace `assets/splash.svg` (or a PNG/JPG and set `splash.image` in `config.yaml`). Copy the project onto the Pi; the new splash is what boots next time.

## Shift and GPS

After login, **Start trip** logs a GPS point every few seconds into `data/bcu.sqlite`. Without a receiver the unit walks a mock path around Adelaide CBD so you can test the trace. On a Pi, plug in GPS (gpsd or NMEA on `/dev/ttyUSB0`); `gps.mode: auto` will use hardware when it appears.

Ended shifts are written as `data/shift-<id>.json`. When the Pi joins an SSID listed under `network.allowed_ssids`, it will POST those files to `network.upload_url` once you add a server.

## Time

Whenever Wi-Fi is up the unit asks the OS to use NTP (`timedatectl` on Linux) and reads network time. Set `timezone: Australia/Adelaide` in config.

## Raspberry Pi kiosk

Copy this folder onto the Pi, then:

```bash
chmod +x scripts/install-pi.sh scripts/kiosk.sh
./scripts/install-pi.sh
sudo reboot
```

Works on Pi 3B+; the same Python/Chromium kiosk path is fine on Pi 4/5.

NFC badge tap is intentionally not wired yet — the badge screen is the placeholder for that.

## Photos

Your reference shots are in `docs/reference/`. More screens later can tighten menus that are still stubs (alarms, concentrator call, ticket slot).
