# HYS Very Cool Racing Game

A small browser-based 3D arcade driving prototype focused on responsive handling, drifting, and phone-as-steering-wheel controls.

The project deliberately stays lightweight: the game and controller are plain HTML/CSS/JavaScript, while a small Python server hosts the files and relays controller input over WebSockets.

## Features

- Four low-poly 3D circuits with distinct layouts, elevation, banking, alternate routes, barriers, and checkpoints
- Arcade driving with suspension, weight transfer, jumps, drifting, and skid marks
- Third-person chase camera
- Keyboard controls
- Wireless phone controller with calibrated motion steering and QR pairing
- Short room codes so each phone controls the intended game
- Countdown, ordered checkpoints, run timing, and locally saved personal best
- Separate local personal bests and translucent, non-colliding replay ghosts for every track
- Procedural engine, tire, impact, landing, checkpoint, and finish audio
- Run restarting from either the keyboard or phone
- No package installation, database, or build step

## Controls

### Keyboard

| Input | Action |
| --- | --- |
| `W` / Up Arrow | Accelerate |
| `S` / Down Arrow | Brake or reverse; initiates a drift while steering at speed |
| `A` / Left Arrow | Steer left |
| `D` / Right Arrow | Steer right |
| `Space` | Initiate a drift |
| `R` | Restart the run and countdown |

### Phone

1. Open the game on a computer.
2. Scan the QR code displayed in the upper-left corner (or open the fallback controller link).
3. Confirm the room code if it was not included automatically.
4. Hold the phone like a steering wheel and tap **Calibrate Center**.
5. Rotate the phone to steer. Use the large accelerator and brake buttons to drive.
6. Hold **Restart** briefly to begin a fresh countdown and timed run.

The controller uses orientation relative to the calibrated physical phone pose, so it does not require iPhone orientation lock.

## Run locally

Requirements: Python 3.9 or newer and a WebGL-capable browser.

```bash
python3 server.py
```

Open <http://127.0.0.1:4173>.

Keyboard driving works immediately. For the most reliable iPhone motion-sensor testing, use the deployed HTTPS version described below; mobile Safari requires a secure page for orientation permission.

## Free deployment with Render

This repository includes `render.yaml`, so it can be deployed without entering custom build commands.

1. Push the repository to GitHub.
2. Sign in to [Render](https://render.com/).
3. Select **New → Blueprint**.
4. Connect this repository.
5. Confirm the free web service and select **Deploy Blueprint**.
6. Open the generated `onrender.com` address on the computer.
7. Use the controller link shown inside the game on the phone.

Render supplies HTTPS and forwards secure WebSocket connections to the Python server. Free services can sleep after a period without traffic, so the first visit after inactivity may take longer.

## Project structure

```text
.
├── dist/
│   ├── index.html       # Game, track, rendering, and driving physics
│   ├── controller.html  # Mobile steering controller
│   └── vendor/          # Small bundled QR generator and its license
├── server.py            # Static server and WebSocket room relay
├── render.yaml          # Render deployment configuration
└── README.md
```

## Design scope

This is an early arcade time-trial prototype rather than a complete racing game. It intentionally does not include accounts, matchmaking, multiplayer races, progression, online leaderboards, or server-side persistent data.

## Technical notes

- Controller messages contain only normalized steering, throttle, brake, and discrete restart input.
- Stale or disconnected controller input returns safely to neutral.
- One controller can occupy a room at a time.
- Room state is kept in memory and resets whenever the server restarts.
- Personal best time and its replay ghost are stored only in the desktop browser's local storage.
- The four tracks are **Nordridge**, **Alpine Pass**, **High Velocity**, and **Summit Run**.
- The server uses only Python's standard library.
