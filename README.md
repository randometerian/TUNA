# TUNA

A fully keyboard-driven terminal music player with real-time audio visualizer.

```
 ████████╗██╗   ██╗███╗  ██╗ █████╗
    ██╔══╝██║   ██║████╗ ██║██╔══██╗
    ██║   ██║   ██║██╔██╗██║███████║
    ██║   ██║   ██║██║╚████║██╔══██║
    ██║   ╚██████╔╝██║ ╚███║██║  ██║
    ╚═╝    ╚═════╝ ╚═╝  ╚══╝╚═╝  ╚═╝
```

## Features

- **Real-time audio visualizer** — FFT-based bars colored by album art palette
- **Dynamic color theming** — UI recolors per song using k-means on album art
- **ASCII album art** — 56×14 character cover art with xterm-256 colors
- **Full keyboard control** — every action reachable without a mouse
- **Playlist management** — create, delete, add/remove tracks (JSON storage)

## Requirements

- Python ≥ 3.11
- mpv (audio playback)
- mutagen (tag reading)
- Pillow (image processing)
- numpy (FFT computation)
- pyaudio + portaudio (audio capture for visualizer)

## Installation

### Arch Linux (AUR)
```bash
yay -S tuna-music
```

### AppImage
Download the latest `.AppImage` from [releases](https://github.com/randometerian/TUNA/releases) and run:
```bash
chmod +x TUNA-*.AppImage
./TUNA-*.AppImage
```

### pip
```bash
pip install .
tuna
```

## Usage

1. Put audio files in `~/Music/tuna/`
2. Run `tuna`
3. Press `?` for help

## Keybindings

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `n` / `p` | Next / Previous track |
| `[` / `]` | Seek −5 / +5s |
| `+` / `-` | Volume |
| `s` | Toggle shuffle |
| `r` | Cycle repeat |
| `Tab` | Toggle focus (sidebar ↔ tracklist) |
| `↑` / `↓` | Navigate |
| `←` / `→` | Switch playlist |
| `PgUp` / `PgDn` | Jump 10 tracks |
| `Home` / `End` | First / Last track |
| `/` | Search |
| `e` | Playlist settings (custom playlists) |
| `?` | Help |
| `q` | Quit |

## Configuration

- Playlists: `~/.config/tuna/playlists/`
- Cover cache: `~/.config/tuna/cache/`
- Config: `~/.config/tuna/config.json`

## License

MIT