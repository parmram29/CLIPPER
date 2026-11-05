# CLIPPER

Turn a long video into vertical clips for TikTok, Reels and Shorts, from the command line.

Give it a YouTube link. It reads the transcript, asks an AI model which moments are worth clipping, cuts them to 9:16, adds word-by-word captions and a hook line, and writes a title, caption and hashtags for each clip. When it's done, everything is in one folder you can AirDrop to your phone.

> **Built on top of an existing project.**
> CLIPPER isn't written from scratch. It's built on top of
> [yt-short-clipper](https://github.com/jipraks/yt-short-clipper) by Aji Prakoso
> ([@jipraks](https://github.com/jipraks)), which is MIT licensed. The core of the video pipeline is
> theirs: grabbing a section of a video, picking highlights with an AI model, face-tracked cropping,
> burned-in captions and hook text. The original is a Windows desktop app. This repo keeps its Python
> pipeline, drops the app, and adds a command line, long-video handling, a different clip-picking prompt,
> captions and hashtags, and ways to review and export the results. [Credits and license](#credits-and-license)
> spells out exactly what came from where.

## What happens when you run it

1. It fetches the video's captions from YouTube, with word timings.
2. An AI model reads the transcript and picks the best moments, each one to two minutes long, and scores how likely people are to watch, share and argue about them.
3. It downloads just those sections (not the whole video) and crops them to vertical.
4. It burns in word-by-word captions and a hook line at the start.
5. It writes a cover title, three caption options, hashtags, search keywords and a pinned comment for each clip.
6. If you ask, it copies the finished clips into one folder.

The video needs captions on YouTube. Auto-generated ones are fine, but there's no speech-to-text yet, so a video with no captions at all won't work.

## Quick start

You need Python 3.10 or newer, and `ffmpeg` (with libass) on your PATH. On a Mac that's `brew install ffmpeg`.

```bash
git clone https://github.com/parmram29/CLIPPER
cd CLIPPER
bash setup.sh
source .venv/bin/activate
```

`setup.sh` makes a virtual environment, installs everything, creates a `.env` file for you to fill in, and ends with a health check. Face tracking is optional, and the script skips it with a short note if your machine can't install it (more on that under [Troubleshooting](#troubleshooting)).

### Fill in `.env`

You need at least your AI key.

| Setting | What it's for |
|---|---|
| `CLIPPER_API_KEY` | Your key for the AI model. Required. |
| `CLIPPER_BASE_URL` | Where the model lives. The default is Alibaba's Qwen (Singapore). Any OpenAI-compatible service works. |
| `CLIPPER_MODEL` | Which model to use. The default is `qwen-plus`; `qwen-flash` is cheaper and fine for picking highlights. |
| `CLIPPER_NICHE` | What your account is about, like `reality TV drama`. Optional. Used for captions. |
| `CLIPPER_REGION` | Where your audience is, like `United States`. Optional. Used for trends. |

Alibaba keys belong to a region. If yours came from the Beijing side, set `CLIPPER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`.

### Add your YouTube cookies

YouTube may refuse to serve a video unless the request looks like it comes from a logged-in browser. So CLIPPER wants a `cookies.txt` exported from one. A browser extension such as "Get cookies.txt LOCALLY" can do it. Export for youtube.com only, not for all sites, and save the file as `cookies.txt` in the project folder.

That file is a copy of your logged-in session, so treat it like a password. It's git-ignored, but don't share it, and consider using a separate YouTube account for this.

### Check your setup

```bash
clipper doctor --check-ai
```

This checks ffmpeg, the Python libraries, your cookies, and your AI key (with one tiny test call). Anything marked `[FAIL]` needs fixing. `[WARN]` lines are optional extras.

### Make your first clips

```bash
clipper run "https://www.youtube.com/watch?v=..." --top 3 --export --open
```

That finds highlights, renders the best three, writes their captions and hashtags, copies the clips into a folder on your Desktop, and opens it. The clips are also kept under `out/sessions/<date-time>/clips/`.

## Commands

| Command | What it does |
|---|---|
| `clipper doctor` | Checks your setup. Add `--check-ai` to test the AI key. |
| `clipper find URL` | Picks highlights and saves them as a session. |
| `clipper process SESSION` | Renders clips from a saved session. |
| `clipper run URL` | `find` and `process` in one go. |
| `clipper social SESSION` | Writes (or rewrites) titles, captions and hashtags. |
| `clipper export SESSION` | Copies finished clips into one folder. |
| `clipper review SESSION` | Writes a spreadsheet so you can rate the picks. |
| `clipper eval review.csv` | Compares your ratings with the AI's scores. |
| `clipper trends` | Shows or refreshes the trend list that captions use. `--diagnose` tests web search step by step. |

Options you'll probably want:

- `--direction "skip the intro; use 12:30 - 13:40"` steers the AI. A clock range you spell out is used exactly.
- `--clips 8` is how many highlights to ask for (default 5). `--top 3` is how many to render.
- `--pick 1,3-4` renders specific clips from a saved session. Clips that are already rendered are skipped on a re-run.
- `--reframe` controls how 16:9 becomes 9:16. `auto` (the default) uses face tracking if it's installed and a centered crop if not. You can also say `face`, `centered`, or `original` to keep the source framing. For centered clips, `--background blurred` (default) puts a blurred copy of the video behind it, and `black` gives plain bars.
- `--export`, `--export-to DIR` and `--open` copy the finished clips somewhere handy.
- `--no-captions`, `--no-hook` and `--no-social` skip those steps.

## How it picks clips

The default profile, `viral`, looks for the moments people watch twice and send to a friend: drama and beef, arguments, scandal and secrets, wild stories, WTF moments, funny punchlines, roasts, spicy-but-appropriate moments (flirting, innuendo, getting caught, never explicit), strong emotion, hot takes, and cliffhangers. It steers clear of slurs aimed at groups, graphic violence, explicit sexual content, and private information.

Every pick gets a 1 to 10 score from a fixed rubric (hook, intensity, surprise, shareability, clean ending) and a category. The score is the AI's opinion. [Rating the picks](#rating-the-picks) shows how to find out whether its taste matches yours.

`--profile podcast` is the original prompt from yt-short-clipper, tuned for podcasts. `--prompt-file my-niche.txt` swaps in your own. It has to contain `{transcript}` and `{num_clips}`, and `clipper/viral_prompt.py` is a good place to start.

Clips come out around one to two minutes long. The original project only keeps picks of 58 to 120 seconds, and a few seconds of padding get added around each.

## Long videos and streams

A video longer than about 37 minutes isn't sent to the model in one piece. CLIPPER splits the transcript into overlapping 30-minute windows (`--window-minutes` and `--overlap-minutes` change that), asks for candidates in each window, and drops duplicates of the same moment. Then one last call scores all the survivors together, so a 7 from a quiet stretch means the same thing as a 7 from a busy one. `--no-rerank` skips that last call. Shorter videos go through in a single call, like the original.

## Captions, hashtags and trends

Each clip gets a cover title, three caption options, 3 to 5 hashtags, a few search keywords, and a pinned comment. The three captions take different angles, a tease, a question and a hot take, so you can try them against each other. Each one is under 150 characters with the hook at the start and no spoilers. It all lands in `<session>/social/`, and `clipper export` puts the captions in a `captions.txt` you can copy from.

The thinking behind them comes from secondary sources, not TikTok itself (TikTok's own explanation wasn't reachable when this was built). Videos seem to spread when people watch to the end, replay, share and comment, and the first seconds matter a lot. Nobody outside TikTok knows the real weights, so there are no numbers in the prompt. A caption can't rescue a weak clip. It can set up the first seconds, add words people search for, and invite a reply. The length limits are this project's choices, not TikTok rules.

### Trends

Captions can also use what's trending. TikTok has no official feed for that, so CLIPPER does web research instead. It asks a search-enabled Qwen model what's trending on TikTok for your niche and region, keeps only findings that match a page the search really returned, and caches the answer for 3 days. It's on by default, and with an Alibaba key it uses Alibaba's own search API, which reports the pages it used. Treat it as an experiment:

- It's research from web pages about TikTok, not data from TikTok.
- On the one account it's been tried on, the search ran and returned a single source, a video page, and the model found nothing usable in it. The search request has been reworked since, and that version hasn't been tried on a real account yet.
- If it finds nothing, the captions are simply written without trends.
- After a failed attempt it waits 24 hours before trying again on its own. `clipper trends --refresh` forces a try and shows what happened.
- To turn it off, set `CLIPPER_AUTO_TRENDS=0`. To use your own list instead, run `clipper trends --init` and fill in `trends.txt`. A list you write always wins.

## Getting clips onto your phone

```bash
clipper export out/sessions/<session> --open
```

This copies the finished clips into one folder (by default `~/Desktop/clipper-exports/<session>`), named like `01 - short-title.mp4`. A `captions.txt` with ready-to-paste captions and the review spreadsheet go in with them. On a Mac, select the clips in Finder and use Share > AirDrop. AirDrop can't be driven from a command line, so CLIPPER stops at the folder. `--to DIR` exports anywhere, for example a folder that syncs to your phone.

## Rating the picks

```bash
clipper review out/sessions/<session>
```

This writes `review.csv`, one row per pick. Import it into Google Sheets (File > Import > Upload), fill the `my_rating` column with a 1 to 10 for the clips you've judged, download it as a CSV, and run:

```bash
clipper eval review.csv
```

The report shows how closely the AI's ranking matches yours, how often its top picks are clips you rated well, and how it does in each category. Change a prompt, make new picks, rate them, and compare. That's how you tell whether a change actually helped. With fewer than about 20 rated clips the numbers are noisy, and the report says so.

## What works today

Checked by hand, on an Intel MacBook Air: setup, `doctor`, and one real episode taken from a YouTube link to a finished vertical clip, using `qwen-flash`.

Covered by automated tests only, so not yet run on real videos with a live model: long-video windows and re-scoring, the `viral` prompt, titles and captions, export, the review sheet, and `eval`. The tests run against a small fake AI server and synthetic video, so they check the logic, not how good the AI's choices are. That's what `clipper eval` is for.

Known gaps:

- Videos need YouTube captions. There's no speech-to-text fallback yet.
- Clip length is fixed at roughly one to two minutes.
- Posting is manual. TikTok's API only allows private posts until an app is audited.
- Only clip footage you have permission to use. YouTube's terms and copyright law apply to what you download and post.

## Troubleshooting

Run `clipper doctor` first. Most problems show up there.

**`clipper: command not found`**: activate the environment with `source .venv/bin/activate`.

**`No subtitle available`**: the video has no captions, not even auto-generated ones. Try another video.

**YouTube says 403 or the cookies are stale**: export a fresh `cookies.txt`.

**ffmpeg is missing, or `brew install ffmpeg` runs for hours (Intel Macs)**: Homebrew has stopped building ready-made packages for Intel Macs, so it compiles everything from source ([discussion](https://github.com/orgs/Homebrew/discussions/7044)). A prebuilt ffmpeg binary works fine. Download one (evermeet.cx publishes Intel builds), put it in `~/bin`, and add that folder to your PATH. `clipper doctor` should then say ffmpeg has libass.

**`pip` can't find `mediapipe` (Intel Macs)**: MediaPipe's current releases only ship Mac wheels for Apple Silicon, and the last release with Intel wheels covers Python 3.9 to 3.12 only ([PyPI files](https://pypi.org/project/mediapipe/#files)). `setup.sh` skips it, and CLIPPER uses a centered crop instead. For face tracking on an Intel Mac, make a fresh environment with Python 3.9 to 3.12 and run `pip install 'clipper[face]'`. On Debian or Ubuntu, face tracking also needs `sudo apt install libegl1 libgles2`.

**`CERTIFICATE_VERIFY_FAILED`**: some Python installs on macOS ship without a list of trusted certificates. CLIPPER switches to the one from `certifi` automatically. If it still fails, something on your network (a work or school network, a VPN, or security software) may be intercepting HTTPS connections.

**`AllocationQuota.FreeTierOnly` or "free quota has been exhausted"**: that's Alibaba refusing the request, not CLIPPER. It happens when a model's free allowance is used up and the "free quota only" switch is on, which stops Alibaba from billing you. Either turn the switch off for that model in the Alibaba console (their [docs](https://help.aliyun.com/en/model-studio/new-free-quota) say it can take about 30 minutes, and an account that isn't identity-verified has to verify and top up first), or use a model that still has free quota. Check prices before you switch to paying.

**Trends come back empty**: run `clipper trends --diagnose`. It sends each web-search request form on its own, shows whether Alibaba accepted or refused it (with the reason), lists the pages the search found, and prints what the model says when it isn't boxed into a strict format. If the pages are off-topic or in another language, the search engine just isn't finding what you need. Captions work without trends, so `CLIPPER_AUTO_TRENDS=0` is a fine answer.

## Credits and license

**CLIPPER is built on top of [yt-short-clipper](https://github.com/jipraks/yt-short-clipper) by Aji Prakoso (jipraks), MIT licensed.** Thank you to them for releasing it openly. This project wouldn't exist without it.

The work is split like this:

| Part | Files | Where it came from |
|---|---|---|
| Subtitles, word timings, downloading a section | `subtitle_*.py`, `json3_parser.py`, `video_processor.py` | yt-short-clipper (small edit) |
| Picking highlights with the AI | `highlight_finder.py`, `prompts.py` | yt-short-clipper (one bug fixed) |
| Vertical crop (face tracking or centered), captions, hook text, GPU encoder detection | `portrait.py`, `caption_generator.py`, `hook_generator.py`, `gpu.py` | yt-short-clipper (face tracking made optional) |
| Small helpers: paths, constants, cookie check, subtitle parsing, watermark | `helpers.py`, `constants.py`, `cookies.py`, `srt_parser.py`, `watermark.py` | yt-short-clipper (one comment changed) |
| Session flow and rendering | `session.py`, `clip_processor.py` | yt-short-clipper (long-video windows plugged in) |
| Command line and settings | `cli.py`, `config.py` | added here |
| Long-video windows and re-scoring | `windowing.py` | added here |
| The `viral` prompt and profiles | `viral_prompt.py`, `profiles.py` | added here |
| Titles, captions, hashtags, trends | `social.py`, `trends.py`, `trend_research.py`, `llm.py` | added here |
| Review sheet, export, rating check | `review.py`, `export.py`, `evaluation.py` | added here |
| Setup script, config, tests | `setup.sh`, `pyproject.toml`, `tests/` | added here |

What was dropped from the original: its desktop app, the in-app wallet, the Repliz uploader, and the Windows packaging. [UPSTREAM.md](UPSTREAM.md) has the full list of what was kept, dropped and changed, including every edit made to the original code.

One more thing for the record: the first commit in this repo imported a different MIT project, [Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill) by op7418, as a starting point. It was replaced by the yt-short-clipper pipeline in the next commit, and none of its code is in the current tree. It's only in the git history.

**License:** MIT. The original copyright notice from yt-short-clipper is kept in [LICENSE](LICENSE), as the license requires.

## Development

```bash
pip install -e ".[dev]"    # setup.sh already does this
pytest
```

The tests run against a small fake AI server on your own machine, so they need no API key and spend nothing. The face-tracking tests are skipped when MediaPipe isn't installed. When it is, the first run downloads a 4 MB face model.
