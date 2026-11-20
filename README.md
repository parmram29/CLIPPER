## Copyright

Original pipeline: Copyright (c) 2026 Aji Prakoso, MIT License (see LICENSE).
Additions and modifications: Copyright (c) 2026 Parmanand Ramcharran.

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

You need Python 3.11 or newer, and `ffmpeg` (with libass) on your PATH. On a Mac that's `brew install ffmpeg`.

```bash
git clone https://github.com/parmram29/CLIPPER
cd CLIPPER
bash setup.sh
source .venv/bin/activate
```

`setup.sh` makes a virtual environment, installs everything (prebuilt packages only, nothing is compiled), creates a `.env` file for you to fill in, and ends with a health check. Face tracking is optional, and the script skips it with a short note if your machine can't install it (more on that under [Troubleshooting](#troubleshooting)).

### Fill in `.env`

You need at least your AI key.

| Setting | What it's for |
|---|---|
| `CLIPPER_API_KEY` | Your Claude key from the [Claude Console](https://platform.claude.com/settings/keys). It starts with `sk-ant-`. Required. |
| `CLIPPER_MODEL` | Which Claude model. The default is `claude-opus-5-5`. `claude-sonnet-5-5` costs about half as much per token and `claude-haiku-5-5` about a fortieth. Optional. |
| `CLIPPER_EFFORT` | How hard Claude thinks before it answers: `low`, `medium`, `high`, `xhigh` or `max`. Optional. Left empty it uses the model's own default (`medium` on `claude-opus-5-5` and `claude-haiku-5-5`, `high` on most others). `low` makes Claude think less, which is quicker and cheaper; `high` and above think more. |
| `CLIPPER_MAX_FPS` | Frames per second a clip keeps. The default is `30`: 60 fps footage is thinned to 30, which halves the work. `off` keeps the source's rate. Optional. |
| `CLIPPER_MAX_HEIGHT` | The tallest video to download for a clip. The default is `720` for the centered layout and `1080` otherwise. Set `1080` for the sharpest centered clips. Optional. |
| `CLIPPER_NICHE` | What your account is about, like `reality TV drama`. Optional. Used for captions. |
| `CLIPPER_REGION` | Where your audience is, like `United States`. Optional. Only used if you turn trend research on. |

The key is for Anthropic's API, which is billed separately from a Claude.ai subscription: make the key in the Console and add a few dollars of credit there. (Claude Max, Ultra and Team plans include monthly API credits that you claim by linking a Console organization to the plan; Free and Pro plans don't. See [API credits for subscribers](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers).) Prices are per million tokens: `claude-opus-5-5` is $4 in and $20 out, `claude-sonnet-5-5` $2 and $10, `claude-haiku-5-5` $0.10 and $0.50 (for prompts up to 100,000 tokens), as listed on Anthropic's [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-11-20. Claude thinks before it answers, and that thinking is billed as output tokens. After every run CLIPPER prints the real token counts (`AI token usage`), so you can work out what a run cost.

**Using a different AI service.** Any OpenAI-compatible service still works (Alibaba's Qwen, OpenAI, a local server). Put that service's own key in `CLIPPER_API_KEY`, and set `CLIPPER_BASE_URL` to its address and `CLIPPER_MODEL` to its model. For Alibaba (Singapore) that's `CLIPPER_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1` and `CLIPPER_MODEL=qwen-plus`; Alibaba keys belong to a region, and a Beijing key needs `https://dashscope.aliyuncs.com/compatible-mode/v1`.

A Claude key is only ever sent to Anthropic. If `.env` still holds an address or model from another service and your key starts with `sk-ant-`, CLIPPER ignores those two lines, tells you so (in `clipper doctor` and at the start of a run), and uses Claude; you can delete the old lines. `CLIPPER_PROVIDER=anthropic` is only for pointing Claude at a gateway of your own.

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

### Or use the app (no typing)

Once it's set up you don't need the Terminal. In Finder, open the CLIPPER folder and double-click **`CLIPPER.command`**. A window opens (leave it open) and your browser shows the app:

1. Paste a YouTube link.
2. Pick a style (drama and fights, funny and wild, or podcasts), how many clips you want, or tick "Let me choose the moments myself" to see everything it found first.
3. Watch the progress. When it's done, each clip has a player, its captions and hashtags with Copy buttons, and (green) the hashtags that came from what's trending right now.
4. "Copy to Desktop folder" makes the folder you AirDrop from.

The first time, `CLIPPER.command` sets itself up (a few minutes, needs internet), and it repairs itself if you move the folder. If macOS says the file "can't be opened because Apple cannot check it", right-click it, choose Open, and confirm once. To stop CLIPPER, close that window. The **Setup** page checks that everything is ready and shows how each trend source is doing.

For a shortcut on the Desktop, right-click `CLIPPER.command`, choose **Make Alias**, and drag the alias there. A shortcut made with `ln -s` works too. A plain *copy* of the file can only find the app if the folder is `~/CLIPPER` (or `Documents`, `Desktop` or `Downloads`, or named in `CLIPPER_HOME`); otherwise it says so and stops.

Under the hood the app runs the same `clipper` commands, so it and the Terminal never disagree. It runs only on your computer (it listens on `127.0.0.1`, never the network), lets in only the browser tab the launcher opened, and needs nothing beyond what CLIPPER already uses. From a Terminal, `clipper gui` starts it too.

## Commands

| Command | What it does |
|---|---|
| `clipper gui` | Opens the point-and-click app in your browser (same as double-clicking `CLIPPER.command`). |
| `clipper doctor` | Checks your setup. Add `--check-ai` to test the AI key. |
| `clipper find URL` | Picks highlights and saves them as a session. |
| `clipper process SESSION` | Renders clips from a saved session. |
| `clipper run URL` | `find` and `process` in one go. |
| `clipper social SESSION` | Writes (or rewrites) titles, captions and hashtags. |
| `clipper export SESSION` | Copies finished clips into one folder, each with a matching `.txt` of its captions and hashtags. |
| `clipper check SESSION` | Checks that most of the picks are drama scenes (6 of 8 by default). Add `--rejudge` to score picks you already have. Exit status 1 if it fails. |
| `clipper review SESSION` | Writes a spreadsheet so you can rate the picks. |
| `clipper eval review.csv` | Compares your ratings with the AI's scores. |
| `clipper trends` | Shows the trends captions use. `--live` reads the live sources now and says how each went; `--init-sites` lets you add trend websites; `--diagnose` tests the AI web search. |

Options you'll probably want:

- `--direction "skip the intro; use 12:30 - 13:40"` steers the AI. A clock range you spell out is used exactly.
- `--clips 8` is how many highlights to ask for (default 5). `--top 3` is how many to render.
- `--pick 1,3-4` renders specific clips from a saved session. Clips that are already rendered are skipped on a re-run.
- `--reframe` controls how 16:9 becomes 9:16. `auto` (the default) uses face tracking if it's installed and a centered crop if not. You can also say `face`, `centered`, or `original` to keep the source framing. For centered clips, `--background blurred` (default) puts a blurred copy of the video behind it, and `black` gives plain bars.
- `--export`, `--export-to DIR` and `--open` copy the finished clips somewhere handy.
- `--no-captions`, `--no-hook` and `--no-social` skip those steps.

## How it picks clips

The default profile, `viral`, looks for the moments that stop a thumb: drama and beef, arguments, scandal and secrets, wild stories, WTF moments, funny punchlines, roasts and insults, spicy-but-appropriate moments (innuendo, getting caught, never explicit), and cliffhangers. Every clip has to contain a confrontation, an accusation or insult, someone being caught out, a shocking reveal, a WTF moment, or a punchline that lands, and it has to open on that line. Two people calmly talking about feelings or what they want in a partner is not a clip, however sweet. It steers clear of slurs aimed at groups, graphic violence, explicit sexual content, and private information.

Every pick gets a 1 to 10 score from a fixed rubric (hook, intensity, surprise, shareability, clean ending) and a category. The score is the AI's opinion. [Rating the picks](#rating-the-picks) shows how to find out whether its taste matches yours.

`--profile podcast` is the original prompt from yt-short-clipper, tuned for podcasts. `--prompt-file my-niche.txt` swaps in your own. It has to contain `{transcript}` and `{num_clips}`, and `clipper/viral_prompt.py` is a good place to start.

Clips come out around one to two minutes long. The original project only keeps picks of 58 to 120 seconds, and a few seconds of padding get added around each.

The AI is told to return 60 to 120 seconds, but smaller models often hand back just the moment itself, 10 to 40 seconds. On the first real episode it was tried on, all 21 picks were that short, and the run ended with "No valid highlights found". So CLIPPER now widens a short pick with the conversation around it, to roughly 70 to 80 seconds: at most 3 seconds go before the moment and the rest after it (the fallout), so the clip still opens on the line the AI chose as its hook, and both ends snap to the edge of a subtitle line so no sentence is cut in half. (An earlier version put about 15 seconds of set-up in front, which buried the opening.) The log says `Widened ...` for each one, and the AI's original times are kept in the session data as `model_start_time` and `model_end_time`. A time range you ask for yourself with `--direction` is never widened, and a pick that is too long is still skipped.

### The drama check: 6 of every 8 clips

This account is about drama, so "mostly drama" is a requirement, not a hope. Of the clips CLIPPER keeps, **at least 75% (6 of 8) must be drama scenes**. The rest may be anything, unless you say otherwise.

The AI that picks the clips also labels them, and on the first real episode it called calm dating talk "drama" (and one pick was a plea to subscribe). So the label is not trusted. Instead:

1. **A separate judgement.** One extra AI call scores every candidate for drama (0 to 10) and for how strong its first ten seconds are (0 to 3), with a prompt that says plainly that most talk is calm. A pick counts as a drama scene at 6 or more.
2. **Drama comes first.** The candidates are ranked drama scenes first, then by how dramatic and how strong the opening is. All candidates are ranked, not just the first few the AI happened to list. `--top` uses the same order.
3. **A second look.** If the first pass finds too few drama scenes, CLIPPER searches the video again asking only for confrontations, accusations, insults, reveals and WTF moments, and judges those too. (It doesn't do this if you gave your own `--direction`.)
4. **The check.** `clipper find` and `clipper run` print a report: how many picks are drama scenes, the score and category of each, which ones fall short, and which have a flat opening. `clipper check SESSION` prints it again and exits with status 1 if the bar isn't met. A pick that was never judged counts as not drama, so the check can't pass on the AI's own say-so.

Change the bar with `--drama-share 6/8`, `75%` or `0.75` (or `CLIPPER_DRAMA_SHARE` in `.env`): `1.0` means all drama, `0.5` allows a mixed account, `0` turns the whole thing off. `clipper check SESSION --rejudge` scores picks you already have and saves the scores. The check is on by default for the `viral` profile only; `--profile podcast` and your own `--prompt-file` don't get it unless you ask.

What this can and can't promise: the tests prove the rules (the bar, the ranking, the second look, the report). Whether the AI's taste in drama matches yours is something only the report and your own eyes can say, so look at the report, and rate a few clips with [`clipper review`](#rating-the-picks).

## Long videos and streams

A video longer than about 37 minutes isn't sent to the model in one piece. CLIPPER splits the transcript into overlapping 30-minute windows (`--window-minutes` and `--overlap-minutes` change that), asks for candidates in each window, and drops duplicates of the same moment. Then one last call scores all the survivors together, so a 7 from a quiet stretch means the same thing as a 7 from a busy one. `--no-rerank` skips that last call (it is ignored while the drama check is on, because that call is also what judges the drama). With the drama check off, shorter videos go through in a single call, like the original.

## Captions, hashtags and trends

Each clip gets a cover title, three caption options, 3 to 5 hashtags, a few search keywords, and a pinned comment. The three captions take different angles, a tease, a question and a hot take, so you can try them against each other. Each one is under 150 characters with the hook at the start and no spoilers. It all lands in `<session>/social/`, and `clipper export` gives every clip a matching `.txt` (same name as the video) with its captions and hashtags, plus a `captions.txt` holding all of them.

The thinking behind them comes from secondary sources, not TikTok itself (TikTok's own explanation wasn't reachable when this was built). Videos seem to spread when people watch to the end, replay, share and comment, and the first seconds matter a lot. Nobody outside TikTok knows the real weights, so there are no numbers in the prompt. A caption can't rescue a weak clip. It can set up the first seconds, add words people search for, and invite a reply. The length limits are this project's choices, not TikTok rules.

### Trends: what's hot right now, read on its own

Hashtags work best when they match what people are looking at *today*, so CLIPPER reads that itself, with nothing for you to do. Every few hours (and at the start of a run, if its copy is out of date) it checks a set of public sources, cross-checks them, and hands the caption writer what is hot and tied to your video:

| Source | What it tells us |
|---|---|
| Google Trends, Wikipedia's most-read pages, Bluesky and Mastodon trending, entertainment and gaming news | what's hot for everyone |
| Google News and Bing News, Google and Bing search suggestions, YouTube's newest videos on the topic | what's being said and searched about *your niche and this video* |
| The video's own tags and hashtags | what the show or streamer already calls it |
| Websites you add (`clipper trends --init-sites`) | any trend or virality site you use |

Something that shows up in several independent sources ranks above something one source says, and something hot that is also about your topic ranks highest. The caption writer is told to use a trend only when it fits the clip, and to skip tags like #fyp.

**What this is not: it doesn't read TikTok.** TikTok publishes no trends feed. Its Creative Center shows an anonymous visitor only a few hashtags and has no public API, and reading the For You page automatically needs a logged-in session, which TikTok's terms forbid and which can get the account limited. CLIPPER never touches TikTok or your account. Hashtags also don't make a video spread by themselves: viewers watching to the end matters far more, and TikTok's own advice is that tags like #fyp don't help.

**Honest status: not yet checked against the live sites.** These sources couldn't be reached from where this was built, so they were tested against realistic samples, not the real feeds. Run `clipper trends --live` (or press "Check the trend sources" on the app's Setup page) to see in plain words how each source went on your computer. A source that fails is skipped, and nothing here can stop a run.

- **Add sites you trust:** `clipper trends --init-sites` makes `trend_sites.txt`. One address per line, with `{query}` replaced by your video's topic. Each page is read politely: CLIPPER says who it is, sends no login, obeys the site's robots.txt, and reads it at most every few hours. A page that needs a login, or fills itself in with JavaScript, gives nothing, and the check says so. A site can add hashtags and supporting text, but it can't put an invented trend at the top by itself.
- **Choose sources:** `CLIPPER_TREND_SOURCES=default,reddit` (Reddit is opt-in because it restricts automated access). `CLIPPER_LIVE_TRENDS=0` turns all of this off, and `--no-auto-trends` skips it for one run.
- **Your own list always wins:** run `clipper trends --init` and fill in `trends.txt`.
- **Country:** `CLIPPER_REGION` ("United States", "UK", ...) picks the country for Google Trends and news.

#### The older AI web research (experimental, off)

CLIPPER can also ask a search-enabled Qwen model what's trending on TikTok, keeping only findings that match a page the search really returned. On the one account it was tried on, Alibaba's search returned pages that were nearly all in Chinese and about TikTok in general, so it is off. `clipper trends --diagnose` shows what the search returns on your account; `CLIPPER_AUTO_TRENDS=1` turns it on, and `--refresh-trends` falls back to it when the live sources give nothing. It needs an OpenAI-compatible service with a web-search switch, so it does nothing when you use Claude; the live sources work with either.

- Without any trends, captions are written from the clip itself plus general best practice. That works fine.

## Getting clips onto your phone

```bash
clipper export out/sessions/<session> --open
```

This copies the finished clips into one folder (by default `~/Desktop/clipper-exports/<session>`), named like `01 - short-title.mp4`. Next to each one is a `01 - short-title.txt` with that clip's title, the hook text shown at its start, its captions, hashtags and pinned comment, and where in the source video it came from, so you can always tell which hashtags belong to which clip. A `captions.txt` holding all of them and the review spreadsheet go in too. On a Mac, select the clips in Finder and use Share > AirDrop. AirDrop can't be driven from a command line, so CLIPPER stops at the folder. `--to DIR` exports anywhere, for example a folder that syncs to your phone.

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

Claude is covered by automated tests, and has not been tried on a live Anthropic account yet. The tests use the real `anthropic` library against a local stand-in server that answers with Anthropic's streaming format and error formats, and they check what is sent (no temperature, room to think, the key in one place), how replies and refusals are read, and what each error says. They can't show that Anthropic accepts every request, so the first real run is the real check: `clipper doctor --check-ai` makes one tiny call.

Covered by automated tests only, so not yet run on real videos with a live model: long-video windows and re-scoring, the `viral` prompt, titles and captions, live trends, export, the review sheet, `eval`, and the app. The tests run against a small fake AI server and synthetic video, so they check the logic, not how good the AI's choices are. That's what `clipper eval` is for.

Known gaps:

- Videos need YouTube captions. There's no speech-to-text fallback yet.
- The app has been tried in a headless Chrome-style browser against a stand-in for the pipeline, not yet on a Mac in Safari with real videos. Clip playback uses the browser's own player, which I couldn't test here.
- The app takes a YouTube link, not a file from your computer or a Twitch link, for the same captions reason.
- Clip length is fixed at roughly one to two minutes.
- The live trend sources haven't been checked against the real sites yet, only against samples (see [Trends](#trends-whats-hot-right-now-read-on-its-own)); `clipper trends --live` shows how each one goes on your computer. The older AI web research doesn't return usable results.
- Posting is manual. TikTok's API only allows private posts until an app is audited.
- Only clip footage you have permission to use. YouTube's terms and copyright law apply to what you download and post.

## Troubleshooting

Run `clipper doctor` first. Most problems show up there.

**`clipper: command not found`**: activate the environment with `source .venv/bin/activate`. If you moved or renamed the project folder (dragging it to another folder in VS Code counts), that won't be enough: the environment remembers the folder it was built in, so its launchers and `activate` script still point at the old location. Open a terminal inside the project folder and rebuild it with `[ -f setup.sh ] && rm -rf .venv && bash setup.sh`. Your `.env`, clips and sessions aren't touched, because only the `.venv` folder is removed. Until then, `.venv/bin/python -m clipper ...` run from inside the project folder still works.

**`No subtitle available`**: the video has no captions, not even auto-generated ones. Try another video.

**YouTube says 403 or the cookies are stale**: export a fresh `cookies.txt`.

**A clip fails with `Unable to handle request` or `FileNotFoundError`**: this is yt-dlp failing to start a download, before anything reaches YouTube. A missing certificate file produces exactly this message. CLIPPER keeps the clips it has already finished, tries the failing one once more with yt-dlp's full debug output, prints a `Diagnostics` block (where certifi's certificate file is and whether it exists, your cookies file, free disk space), and carries on with the remaining clips. Then run the same command again: finished clips are skipped, so only the missing ones are redone. If the diagnostics say the certificate file is missing, `pip install --force-reinstall certifi` fixes it. To see yt-dlp's full output for any download, set `CLIPPER_YTDLP_VERBOSE=1`.

**ffmpeg is missing, or `brew install ffmpeg` runs for hours (Intel Macs)**: Homebrew has stopped building ready-made packages for Intel Macs, so it compiles everything from source ([discussion](https://github.com/orgs/Homebrew/discussions/7044)). A prebuilt ffmpeg binary works fine. Download one (evermeet.cx publishes Intel builds), put it in `~/bin`, and add that folder to your PATH. `clipper doctor` should then say ffmpeg has libass.

**`pip` can't find `mediapipe` (Intel Macs)**: MediaPipe's current releases only ship Mac wheels for Apple Silicon, and the last release with Intel wheels covers Python 3.9 to 3.12 only ([PyPI files](https://pypi.org/project/mediapipe/#files)). `setup.sh` skips it, and CLIPPER uses a centered crop instead. For face tracking on an Intel Mac, make a fresh environment with Python 3.9 to 3.12 and run `pip install 'clipper[face]'`. On Debian or Ubuntu, face tracking also needs `sudo apt install libegl1 libgles2`.

**`CERTIFICATE_VERIFY_FAILED`**: some Python installs on macOS ship without a list of trusted certificates. CLIPPER switches to the one from `certifi` automatically. If it still fails, something on your network (a work or school network, a VPN, or security software) may be intercepting HTTPS connections.

**A clip takes many minutes to make**: after every clip CLIPPER prints a `Timing:` line (`cut`, `layout`, `hook`, `captions`) so you can see which step is slow. The work is video encoding, and the biggest single factor is whether your computer's own video encoder is used. `clipper doctor` has a `video encoder:` line that says which one will be: on a Mac it should say `h264_videotoolbox`, which every Mac has (Intel ones included); `libx264 on the CPU` means none worked, and the reason is on that line. The other two levers are `CLIPPER_MAX_FPS` and `CLIPPER_MAX_HEIGHT` (see the table above).

Measured, and what wasn't: 30 seconds of 60 fps 1080p test footage took 118 s for the four steps before this was changed and 33 s after, on a 4-core Linux machine using only the CPU encoder, and the old and new pictures compared at SSIM 0.996 / PSNR 43.5 dB (differences that small can't be seen). The old `download` step turned out to be re-encoding the whole section with ffmpeg's default (slow) settings: that is yt-dlp's documented behaviour when it is told to cut exactly, and it took 5 minutes for a 78-second section on a MacBook Air. The Mac hardware encoder was not measured, because it only exists on a Mac, so that part is expected to help rather than known to.

**"Anthropic rejected the API key (401)"**: `CLIPPER_API_KEY` in `.env` isn't a valid Claude key. It should start with `sk-ant-`, be on one line with nothing else, and come from the [Console](https://platform.claude.com/settings/keys). A key that was deleted or copied with a missing piece fails the same way. `clipper doctor --check-ai` shows the result of one call.

**"Your Anthropic account is out of credit"**: API use has its own balance, separate from a Claude.ai subscription. Add credit in the Console's [Billing page](https://platform.claude.com/settings/billing).

**"Anthropic does not know the model"**: the `CLIPPER_MODEL` line in `.env` names a model that doesn't exist or that your account can't use. Delete the line to get `claude-opus-5-5`, or use `claude-sonnet-5-5` or `claude-haiku-5-5`.

**"Claude ran out of room before it finished its reply"**: Claude spends part of its allowance thinking, and on a very long window it ran out. Run it again, add `CLIPPER_EFFORT=low` to `.env`, or use a shorter video.

**`AllocationQuota.FreeTierOnly` or "free quota has been exhausted"** (only if you use Alibaba): that's Alibaba refusing the request, not CLIPPER. It happens when a model's free allowance is used up and the "free quota only" switch is on, which stops Alibaba from billing you. Either turn the switch off for that model in the Alibaba console (their [docs](https://help.aliyun.com/en/model-studio/new-free-quota) say it can take about 30 minutes, and an account that isn't identity-verified has to verify and top up first), or use a model that still has free quota. Check prices before you switch to paying.

**Trends come back empty** (only if you turned them on): run `clipper trends --diagnose`. It sends each web-search request form on its own, shows whether Alibaba accepted or refused it (with the reason), lists the pages the search found, and prints what the model says when it isn't boxed into a strict format. If the pages are off-topic or in another language, the search engine just isn't finding what you need. Captions work without trends, so leaving them off is a fine answer.

## Keeping your accounts safe

This repo is public, so it's set up so that nothing private can end up in it.

- **Your key and cookies stay on your computer.** `.env` and cookie files are git-ignored, including the usual names (`cookies.txt`, `www.youtube.com_cookies.txt`, JSON exports) and private keys. `tests/test_repo_hygiene.py` fails if that stops being true, or if any file that isn't git-ignored contains something shaped like an API key, a private key or a cookie line.
- **Treat the cookies file like a password.** Whoever has it can use your YouTube login. Don't commit it, email it or paste it into a chat. If you've shared it anywhere, signing out of YouTube everywhere (or changing your Google password) makes it useless.
- **Where your data goes.** `yt-dlp` talks to YouTube, using your cookies. The transcript of each video goes to the AI service you set in `.env` (Anthropic's Claude by default). If MediaPipe is installed, its face model is downloaded once from Google. CLIPPER itself sends nothing else anywhere and has no telemetry.
- **Dependencies are checked.** `pyproject.toml` only allows versions of its dependencies that had no known security advisory when they were last checked (the date is in the file), and `requirements.txt` pins the exact versions the tests pass on. To check your own install, run `pip install pip-audit` and then `pip-audit`. `setup.sh` installs prebuilt packages only, so no third-party package gets compiled or runs its own build script on your machine.

## Credits and license

**CLIPPER is built on top of [yt-short-clipper](https://github.com/jipraks/yt-short-clipper) by Aji Prakoso (jipraks), MIT licensed.** Thank you to them for releasing it openly. This project wouldn't exist without it.

The work is split like this:

| Part | Files | Where it came from |
|---|---|---|
| Subtitles, word timings, downloading a section | `subtitle_*.py`, `json3_parser.py`, `video_processor.py` | yt-short-clipper (small edit) |
| Picking highlights with the AI | `highlight_finder.py`, `prompts.py` | yt-short-clipper (one bug fixed, short picks widened) |
| Vertical crop (face tracking or centered), captions, hook text, GPU encoder detection | `portrait.py`, `caption_generator.py`, `hook_generator.py`, `gpu.py` | yt-short-clipper (face tracking made optional) |
| Small helpers: paths, constants, cookie check, subtitle parsing, watermark | `helpers.py`, `constants.py`, `cookies.py`, `srt_parser.py`, `watermark.py` | yt-short-clipper (comments only) |
| Session flow and rendering | `session.py`, `clip_processor.py` | yt-short-clipper (long-video windows plugged in) |
| Command line and settings | `cli.py`, `config.py`, `diagnostics.py` | added here |
| Long-video windows and re-scoring | `windowing.py` | added here |
| The `viral` prompt, profiles and the drama check | `viral_prompt.py`, `profiles.py`, `drama.py` | added here |
| Titles, captions, hashtags, trends | `social.py`, `trends.py`, `trend_research.py`, `llm.py` | added here |
| Review sheet, export, rating check | `review.py`, `export.py`, `evaluation.py` | added here |
| Setup script, config, pinned dependencies, tests | `setup.sh`, `pyproject.toml`, `requirements.txt`, `tests/` | added here |

What was dropped from the original: its desktop app, the in-app wallet, the Repliz uploader, and the Windows packaging. [UPSTREAM.md](UPSTREAM.md) has the full list of what was kept, dropped and changed, including every edit made to the original code.

One more thing for the record: the first commit in this repo imported a different MIT project, [Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill) by op7418, as a starting point. It was replaced by the yt-short-clipper pipeline in the next commit, and none of its code is in the current tree. It's only in the git history.

**License:** MIT. The original copyright notice from yt-short-clipper is kept in [LICENSE](LICENSE), as the license requires.

## Development

```bash
pip install -e ".[dev]"    # setup.sh already does this
pytest
```

The tests run against a small fake AI server on your own machine, so they need no API key and spend nothing. The face-tracking tests are skipped when MediaPipe isn't installed. When it is, the first run downloads a 4 MB face model.
