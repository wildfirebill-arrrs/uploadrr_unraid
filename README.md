![Python](https://img.shields.io/badge/python-3.9+-blue) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
# Uploadrr

Uploadrr is a tool for preparing and uploading torrents for Movies, TV Shows, Music, and Adult Content. It automates metadata gathering, screenshot generation, torrent creation, and uploading to various private trackers.

This project is a fork of [Upload-Assistant](https://github.com/L4GSP1KE/Upload-Assistant) by L4G, with the goal of giving users more control over the upload process.

## Features

- Automatic metadata gathering from TMDb/IMDb, MusicBrainz/Discogs/Deezer, and TPDb
- Screenshot generation with customizable settings (tonemapping, frame stats, contact sheets, etc.)
- Torrent creation with multiple client support
- Support for 50+ private trackers
- Batch processing capabilities
- Duplicate detection and prevention
- Customizable signatures and description handling

## Supported Trackers

Uploadrr supports 50+ private trackers. They are grouped by the engine they use:

- **UNIT3D-based**: The majority of supported trackers (check `src/trackers/` for the full list).
- **Gazelle-based**: A handful of trackers using the Gazelle/Luminance engine.
- **AviztaZ Network**: AZ, CZ, PHD
- **NexusPHP-based**: Complete with TEMPLATE for easy add. (Auto-login for captcha based else seed cookies)
- **Custom/API**: Trackers with bespoke upload APIs, including BHD, PTP, HDB, and others.

See `src/trackers/` for the complete, up-to-date list of implementations.


## Installation

### Requirements

- **Python 3.9+**
- **FFmpeg** (video processing and screenshots)
- **Mediainfo** (metadata extraction)

### Clone + Install

1. Clone the repository:
   ```bash
   git clone https://codeberg.org/CvT/Uploadrr.git
   cd Uploadrr
   ```

2. Install dependencies with `uv`:
   ```bash
   uv sync
   ```

   If you do not have `uv`, install it from [https://docs.astral.sh/uv](https://docs.astral.sh/uv) or fall back to:
   ```bash
   pip install -e .
   ```

## Docker / Unraid

A `Dockerfile` and `docker-compose.yml` are included. The image stays alive (tail) so you can exec into it and run the CLI.

### docker-compose

```bash
docker compose up -d
docker exec -it uploadrr /bin/sh
# inside the container:
python3 /app/upload.py /data/path/to/media --trackers BLU HDB
```

Adjust the volume mappings in `docker-compose.yml` to match your Unraid paths (appdata, media, qBittorrent BT_backup).

### Unraid

An Unraid Community Applications template is published at:
https://raw.githubusercontent.com/wildfirebill-unraid/unraid-configs/main/templates/uploadrr.xml

In Unraid, install via Community Apps (search "Uploadrr") or add the template URL manually. The container stays running; use the Unraid console (or `docker exec`) to run commands, e.g. `python3 /app/upload.py /data/movie.mkv --trackers BLU`.

## Configuration

Uploadrr uses a YAML configuration file located at `data/config.yaml`. Copy the example configuration and customize it for your needs:

```bash
cp data/backup/example_config.yaml data/config.yaml
```

### Key Configuration Sections

>[!IMPORTANT]
> For Movies/TV/Anime you need a TMDb API (v3) key from **[TMDb](https://developer.themoviedb.org/docs/getting-started)**
> For Adult Content you should have a TPDb key from **[TPDb](https://theporndb.net/user/api-tokens)**

- **DEFAULT**: Basic settings like TMDb API key, default torrent client, and anonymity preferences
- **ADULT**: Adult config, TPDb key, separate screen API keys if desired, adult anon override, contactsheet settings, etc.
- **SCREENS**: Screenshot generation settings, including API keys for image hosting services
- **SIGNATURES**: Custom signatures for uploads (global and per-tracker)
- **AUTO**: Automation settings like delay between uploads and duplicate detection
- **TRACKERS**: Configuration for supported private trackers (API keys, usernames, etc.)
- **TORRENT_CLIENTS**: Settings for torrent clients (qBittorrent, Deluge, etc.)

>[!TIP]
> See the __[Full Config Guide](https://codeberg.org/CvT/Uploadrr/wiki/Full-Config-Guide)__ for detailed options and supported trackers.

## Usage

Run Uploadrr from the project directory:

```bash
python upload.py [options] <path_to_media>
```

### Command Line Options

**Global Control**

- `--help`: Show available options
- `--debug`: Enable debug logging
- `--deep-debug`: Enable verbose debug logging
- `--unattended`: Skip confirmation prompts
- `--quiet`: Quiet mode — only show summary and errors
- `--full-auto [DIR]`: Full automation (queue, grab description, unattended)
- `--auto-queue DIR`: Automatically queue all items in a directory
- `--delay SECONDS`: Delay between uploads
- `--random`: Randomize upload order
- `--skip-dupe-check`: Skip duplicate checking
- `--no-seed`: Do not add torrent to client

**Content & Metadata**

- `--trackers <list>`: Trackers to upload to (e.g., `blu bhd`)
- `--category {movie,tv,fanres,sports,music}`: Content category
- `--type {disc,remux,encode,webdl,webrip,hdtv}`: Release type
- `--source {Blu-ray,DVD,HDDVD,WEB,HDTV,UHDTV}`: Source media
- `--resolution {2160p,1080p,1080i,720p,576p,480p,other}`: Video resolution
- `--screens <number>`: Number of screenshots to generate
- `--imghost <name>`: Image hosting service
- `--tmdb <id>`: TMDb ID (e.g., `123` or `movie/123`)
- `--imdb <id>`: IMDb ID
- `--year <year>`: Release year
- `--season <n>` / `--episode <n>`: Season or episode override
- `--daily <YYYY-MM-DD>`: Air date for daily/episodic releases

**Adult Content**

- `--xxx`: General XXX content
- `--xscene`: XXX Scene mode
- `--xmovie`: XXX Movie mode
- `--jav`: JAV content
- `--pack`: XXX Pack (mini/mega)

**Torrent & Client**

- `--piece-size-max {1,2,4,8,16}`: Max piece size in MiB
- `--torrent-creation {torf,torrenttools,mktorrent}`: Torrent creation tool
- `--client <name>`: Override default torrent client
- `--qbit-tag <tag>` / `--qbit-cat <cat>`: qBittorrent tag/category
- `--rtorrent-label <label>`: rTorrent label
- `--freeleech [%]`: Freeleech percentage (default 100 if used without value)

**Description & Overrides**

- `--desc "<text>"`: Custom description
- `--desclink <url>`: Pastebin URL for description
- `--descfile <path>`: Local description file
- `--auto-insert-desc`: Use existing `.txt` file for description
- `--manual-title "<text>"`: Override generated title
- `--cover <path/url>`: Custom cover image
- `--banner <path/url>`: Custom banner image

**Flags**

- `--anon`: Upload anonymously
- `--draft`: Send to drafts (BHD)
- `--mod-queue`: Opt into Mod Queue
- `--no-year`: Remove year from title
- `--no-tag`: Remove group tag from title
- `--hardcoded-subs`: Mark as containing hardcoded subtitles
- `--stream`: Stream-optimized encoding
- `--webdv`: Contains Dolby Vision layer
- `--vapoursynth`: Use VapourSynth for screenshots
- `--ffdebug`: Show ffmpeg debug during screenshots
- `--rehash`: Force rehash

### Examples

Upload a movie to default trackers:
```bash
python upload.py /path/to/movie.mkv
```

Upload TV show with custom screenshots:
```bash
python upload.py --screens 8 --trackers BLU HDB /path/to/tv_show/
```

Upload music:
```bash
python upload.py /path/to/album
```

Upload adult content:
```bash
python upload.py --xxx /path/to/adultscene.mp4
```

Full-auto batch upload:
```bash
python upload.py --full-auto /path/to/watch_folder
```


## Contributing

Contributions are welcome. Please feel free to submit issues, feature requests, or pull requests.

## Acknowledgments

All credit for the original codebase goes to [L4G](https://github.com/L4GSP1KE) and contributors to the [Upload-Assistant](https://github.com/L4GSP1KE/Upload-Assistant) project.


## Attributions

This project uses the following open-source tools, APIs, and metadata providers:

<a href="https://ffmpeg.org"><img src="https://upload.wikimedia.org/wikipedia/commons/5/5f/FFmpeg_Logo_new.svg" height="33" alt="FFmpeg logo"></a> 
<a href="https://mediaarea.net/en/MediaInfo"><img src="https://upload.wikimedia.org/wikipedia/commons/1/19/MediaInfo_Logo.svg" height="33" alt="MediaInfo logo"></a> 
<a href="https://www.themoviedb.org/"><img src="https://upload.wikimedia.org/wikipedia/commons/8/89/Tmdb.new.logo.svg" height="33" alt="TMDb logo"></a> 
<a href="https://www.imdb.com/"><img src="https://upload.wikimedia.org/wikipedia/commons/6/69/IMDB_Logo_2016.svg" height="33" alt="IMDb logo"></a> 
<a href="https://musicbrainz.org/"><img src="https://upload.wikimedia.org/wikipedia/commons/f/f2/MusicBrainz_Logo_Mini_%282016%29.svg" height="33" alt="MusicBrainz logo"></a> 
<a href="https://www.discogs.com/"><img src="https://upload.wikimedia.org/wikipedia/commons/6/69/Discogs_record_icon.svg" height="33" alt="Discogs logo"></a> 
<a href="https://www.deezer.com/"><img src="https://upload.wikimedia.org/wikipedia/commons/7/75/Deezer_logo%2C_2023.svg" height="33" alt="Deezer logo"></a> 
<a href="https://theporndb.net/"><img src="https://theporndb.net/images/logo.png" height="33" alt="ThePornDB logo"></a> 


Shout out to the following maintainers of great packages like [imdbinfo](https://github.com/tveronesi/imdbinfo) & [Pillow](https://github.com/python-pillow/Pillow).  
Made with [BDInfoCLI](https://github.com/tetrahydroc/BDInfoCLI) for BDUHD Support.  
Optional additions like [mkbrr](https://github.com/autobrr/mkbrr) provide nice speed gains on newer machines. 
