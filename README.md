# CLIPPER

Turn a long video into vertical 9:16 short-form clips: an LLM picks the highlights from the
transcript, then each one is cut, reframed, captioned word by word, and given a hook overlay.

CLIPPER is a headless CLI built on the pipeline from
[jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper) (MIT). See
[UPSTREAM.md](UPSTREAM.md) for exactly what was kept, dropped, and changed.

## Setup

Needs Python 3.10+ and `ffmpeg` with libass on your PATH (macOS: `brew install ffmpeg`).

```bash
bash setup.sh
source .venv/bin/activate
```

**Face tracking is optional.** It needs MediaPipe, which `setup.sh` tries to install and skips
if there is no build for your machine. Without it, `--reframe auto` (the default) uses a centered
crop and everything else works. On Debian/Ubuntu, face tracking also needs
`sudo apt install libegl1 libgles2`. `clipper doctor` reports both cases.

Then edit `.env`:

| Variable | Meaning |
|---|---|
| `CLIPPER_API_KEY` | key for any OpenAI-compatible endpoint |
| `CLIPPER_BASE_URL` | default: Alibaba Model Studio (Qwen), international endpoint |
| `CLIPPER_MODEL` | default: `qwen-plus` |
| `CLIPPER_COOKIES` | path to a YouTube `cookies.txt` (default `./cookies.txt`) |

Alibaba keys are region-specific; for a Beijing key use
`https://dashscope.aliyuncs.com/compatible-mode/v1`
([docs](https://www.alibabacloud.com/help/en/model-studio/text-generation)).
`cookies.txt` and `.env` are git-ignored. Never commit them.

```bash
clipper doctor --check-ai    # verifies ffmpeg, libraries, cookies, and makes one tiny AI call
```

## Use

```bash
clipper find "https://www.youtube.com/watch?v=..." --clips 5     # pick highlights, writes a session
clipper process out/sessions/<session> --top 3                   # render the 3 best (+ titles/captions)
clipper run "https://www.youtube.com/watch?v=..." --top 3        # both steps at once
clipper run "https://www.youtube.com/watch?v=..." --top 3 --export --open   # ...and put them in a folder
```

Clips land in `<session>/clips/clip_NNN/master.mp4` with a `data.json` (title, description, hook, timings).

Useful options:

- `--direction "skip the intro; use 12:30 - 13:40"` steers the highlight model. An explicit clock range is used exactly.
- `--reframe auto|face|centered|original` and `--background blurred|black`. `auto` (default) tracks faces if MediaPipe is installed and centers otherwise; an explicit `face` without MediaPipe fails before downloading anything. For centered clips the default background is a blurred copy of the video; `--background black` gives plain bars. Face tracking is the slow path; centered is a single ffmpeg pass.
- `--pick 1,3-4` or `--top N` chooses which clips to render. Rendered clips are skipped on re-run.

### What it looks for

`--profile viral` (the default) hunts for the moments people watch twice and send to friends:
drama and beef, arguments, scandal and secrets, wild and outlandish stories, WTF moments, funny
punchlines, roasts and insults, spicy-but-appropriate moments (flirting, innuendo, "caught in the
act", never explicit), strong emotion, hot takes, and cliffhangers. It will not pick clips whose
appeal is a slur or hate aimed at a group, graphic violence, explicit sexual content, or private
information. Each pick gets a `virality_score` from a fixed rubric (hook 0-3, intensity 0-2,
surprise 0-2, shareability 0-2, clean cut 0-1) and a `category`.

`--profile podcast` is upstream's original prompt. `--prompt-file niche.txt` replaces either with your own
(it must contain `{transcript}` and `{num_clips}`; start from `clipper/viral_prompt.py`).

### Long videos and streams

Videos longer than about 37 minutes are not sent in one prompt. The transcript is split into
overlapping windows (`--window-minutes 30`, `--overlap-minutes 2`), each window proposes
candidates, duplicates of the same moment are dropped, and one final call re-scores every
survivor on a single scale, so a 7 from a dull stretch and a 7 from a lively one mean the
same thing. Pass `--no-rerank` to skip that last call. Shorter videos behave as before.

### Captions, hashtags and trends

`process` and `run` write post copy for each clip to `<session>/social/clip_NNN.json` (skipped if no API key
is set; `--no-social` turns it off). `clipper social <session>` writes or rewrites it, `--force` overwrites.
Each clip gets:

- a **cover title** (60 characters);
- **three caption angles** to test against each other: *tease* (promise the payoff, don't give it away),
  *question* (something people will answer in the comments), and *hot take*. Each is at most 150 characters with
  the hook in the first 100, one natural search keyword early, and no spoilers. The tone follows the clip's
  category (a funny clip leaves the punchline out; a drama clip leads with the conflict);
- **3-5 hashtags**: a broad one or two, one or two specific to what happens, and at most one trending tag;
- **search keywords** (what someone might type to find it) and a **pinned comment** that invites replies.

What this is built on, and how sure that is. Guidance about TikTok's For You feed commonly points to
people finishing a video, replaying it, sharing it, commenting and saving it, and to the opening seconds. That
comes from secondary sources (I could not read TikTok's own explanation), and the exact weightings are not public,
so nothing here uses numbers. A caption cannot rescue a weak clip: it sets up the first seconds, adds words people
search for, and invites a reply. The limits above are CLIPPER's choices, not platform rules. Sources also disagree
on TikTok's caption length limit, and say only the first 80-125 characters show before "more".

**Trends.** There is no official feed of TikTok trends. The
[Creative Center](https://ads.tiktok.com/help/article/how-to-use-trends) shows trending hashtags, videos and creators by region and industry,
but only as a dashboard. So you paste in what is trending, and the model is told to use *only* that, since its own idea
of "trending" is months out of date:

```bash
clipper trends --init          # creates ./trends.txt with a template and where to look
clipper trends                 # shows what will be used, how old it is, and which hashtags it offers
clipper social out/sessions/<session> --niche "reality TV drama" --trends trends.txt
```

A trend is only used when it genuinely fits the clip, and `trend_tags_used` records which ones were, so you can compare
posts later. The file is flagged stale after 14 days (a CLIPPER default: trends decay fast, so refresh it weekly). With no
trends file the captions use general best practice only and the model is told not to claim anything is trending.
`--niche` (or `CLIPPER_NICHE`) keeps wording and hashtags consistent across an account.

To learn what works for your account, post different angles (tease on one clip, question on the next) and compare
TikTok's own view and watch-time numbers per post. Nothing here measures that for you yet.

### Getting clips onto your phone

```bash
clipper export out/sessions/<session> --open
```

copies the finished clips into one folder (default `~/Desktop/clipper-exports/<session>`) named
`01 - short-title.mp4`, with a `captions.txt` of ready-to-paste captions and the review sheet. On a Mac,
select them in Finder and use Share > AirDrop. (AirDrop can't be driven from a command line, so the
tool stops at the folder.) `--to DIR` exports anywhere, for example a folder that syncs to your phone.
`--export`, `--export-to DIR` and `--open` do the same straight after `process` or `run`.

### Reviewing picks and measuring the prompt

```bash
clipper review out/sessions/<session>      # writes review.csv, one row per pick
```

Import `review.csv` into Google Sheets (File > Import > Upload), fill the `my_rating` column
(1-10) for the picks you have judged, download it as CSV, and run:

```bash
clipper eval review.csv --good 7 --k 3
```

It reports how closely the model's ranking matches yours (Spearman rank correlation), how often its top
picks are clips you rated well, and the breakdown per category. Change a prompt, re-run, rate, and compare:
that is how you tell whether a change actually helped. Under about 20 rated clips the numbers are noisy, and
the report says so. There is no direct Google Sheets sync: that needs a Google login set up in an API project,
and the CSV import takes seconds.

## Status and limits

- **Tested (`pytest`):** config handling, the CLI, highlight selection, windowing and re-scoring against a real local OpenAI-compatible server, session writing, post copy, review/export/eval, the centered and blurred reframes, caption and hook stages on a synthetic video, and face-tracked reframing on a real face photo.
- **Run on a real video:** one hour-long episode, end to end on a Mac, through `qwen-flash`. The windowing, the viral prompt and the re-scoring have only been exercised against a stand-in model, not a live one, so how well the new prompt picks is unmeasured until you rate some picks with `clipper eval`.
- **Needs YouTube captions.** There is no speech-to-text fallback yet, so a video without a caption track fails. Word-by-word captions also come from YouTube's track.
- **Clip length is fixed at about 60-120 s** (upstream's validator keeps only 58-120 s clips).
- **Scores are the model's opinion.** They are only as good as their agreement with your ratings, which is what `clipper eval` measures.
- **Cookies are your logged-in session.** Treat the file like a password.
- Only clip footage you have the rights to use. YouTube's terms and copyright law apply.

## Troubleshooting

**`Could not find a version that satisfies the requirement mediapipe` (`from versions: none`)**
MediaPipe's current releases (1.x) publish macOS wheels for **Apple Silicon only**; the last
release with Intel-Mac wheels is 0.10.21, for Python 3.9-3.12
([PyPI files](https://pypi.org/project/mediapipe/#files)). So an Intel Mac, or a Python running under
Rosetta, on Python 3.13+ has nothing to install. Check yours:

```bash
python3 -c "import sys, platform; print(sys.version.split()[0], platform.machine())"
```

`x86_64` means Intel (or Rosetta); `arm64` means Apple Silicon. Current `setup.sh` no longer fails on this
and runs without face tracking. To get face tracking on Intel, use Python 3.9-3.12 in a fresh venv
(e.g. `brew install python@3.12`) and `pip install 'clipper[face]'`.

## Tests

```bash
pytest
```

The face-tracking tests are skipped when MediaPipe isn't installed.
