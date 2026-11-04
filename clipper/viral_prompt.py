"""The virality-focused highlight prompt and scoring rubric.

Not part of upstream. The prompt keeps upstream's placeholders ({num_clips},
{user_direction}, {output_language}, {video_context}, {transcript}) and its JSON
contract, so upstream's parsing and validation work unchanged. It adds one extra
field, ``category``.
"""

# Tags the model may put in each clip's "category" field. Used for the review sheet
# and for `clipper eval`, so scores can be compared per kind of moment.
CATEGORIES = (
    "drama",
    "argument",
    "scandal",
    "wild",
    "wtf",
    "funny",
    "punchline",
    "roast",
    "spicy",
    "emotional",
    "hot_take",
    "other",
)

# One shared rubric, used by the per-window prompt and by the cross-window re-scoring
# call, so a "7" means the same thing everywhere.
VIRALITY_RUBRIC = """\
Score each clip 1-10 by adding these parts (whole numbers):
* HOOK (0-3): is the first 3 seconds already gripping? A shocking line, an accusation, a
  question, a loud reaction.
* INTENSITY (0-2): emotional heat and stakes: anger, laughter, shock, embarrassment, heartbreak.
* SURPRISE (0-2): something unexpected: a twist, a reveal, an escalation, an absurd turn, a
  line nobody saw coming.
* SHAREABILITY (0-2): would people send it to a friend, quote it, or argue about it in the
  comments?
* CLEAN CUT (0-1): it makes sense without the rest of the video and ends on its payoff.
Most candidates should land between 4 and 7. Give 9-10 only to the rare clip that scores near
the top of every part. Be honest: these scores decide which clips get produced."""

VIRAL_HIGHLIGHT_PROMPT = """You are a short-form editor who knows exactly what stops a thumb on TikTok, Reels and Shorts. You cut the moments people watch twice and send to their friends.

Your output goes straight into production. A duration or format mistake fails the
entire job.

==================================================
TASK (NON-NEGOTIABLE)
=====================

From the transcript below, return EXACTLY {num_clips} segments.

* Never fewer.
* Never more.
* An empty array is forbidden under any circumstances.

The transcript may be one part of a longer video. Timestamps are absolute: copy them from the
transcript exactly as written.

If strong moments are hard to find, still return {num_clips}: take the best available and score
them honestly low.
{user_direction}
==================================================
OUTPUT LANGUAGE (DO NOT GET THIS WRONG)
=======================================

These instructions are in English. What you WRITE is not.

* "title", "description" and "hook_text" MUST be written in casual {output_language}, in an
  everyday spoken register, not formal writing.
* If {output_language} is the language spoken in the transcript, keep the speaker's own words
  when you quote them. Do not paraphrase into cleaner prose.
* If it is a different language, translate naturally into {output_language}. Never mix two
  languages inside one field.
* Field names, the category value, JSON structure and timestamp format stay exactly as
  specified, in English, regardless of {output_language}.

==================================================
WHAT MAKES A CLIP GO VIRAL
==========================

Hunt for these, roughly in order of pull:

1. DRAMA and beef: confrontations, call-outs, fights, betrayals, someone getting exposed.
2. ARGUMENTS and heated debates: raised voices, interruptions, "say that to my face" energy.
3. SCANDAL and secrets: confessions, gossip, receipts, "I can't believe they said that".
4. WILD and outlandish: absurd stories, unhinged behavior, things that sound made up.
5. WTF moments: sudden left turns, shock, dead silence, jaw-drop reactions.
6. FUNNY: punchlines, roasts, savage insults and comebacks, cringe or awkward beats, bits that land.
7. SPICY but appropriate: flirting, innuendo, sneaky "caught in the act" moments, risqué humor.
   Suggestive is fine. Explicit is not.
8. EMOTION: breakdowns, heartfelt confessions, triumphant or devastating reveals.
9. HOT TAKES: bold or controversial opinions people will want to argue about.
10. CLIFFHANGERS and plot twists: a reveal that lands or a question left hanging.

Prefer segments with a complete arc: setup, escalation, payoff. Open on the strongest line you
can, not on throat-clearing. End on the payoff, not mid-thought.

AVOID:

* Intros, outros, sponsor reads, housekeeping, "like and subscribe"
* Small talk, rambling, and topic transitions with no payoff
* Long explanations with no emotion, no conflict and no surprise
* Anything that only makes sense with context from elsewhere in the video

HARD LIMITS (platform safety). Never pick a clip whose appeal is:

* a slur or hate aimed at a protected group (insults and roasts are fine; hate is not)
* graphic violence, or explicit sexual content
* private personal information about a real person
* anything involving harm to minors

SEARCH THE WHOLE TRANSCRIPT:

* Read from the first line to the last before deciding anything.
* The best moments are rarely all near the start. Spread the picks across the full runtime
  unless a direction says otherwise.
* Segments must never overlap each other.

==================================================
DURATION (CRITICAL)
===================

* Every clip MUST run 60-120 seconds.
* Aim for 60-90 seconds.
* Compute duration from the transcript timestamps. Never estimate it from how much text a
  segment contains.

Shorter than 60 seconds -> extend it with context before or after.
Longer than 120 seconds -> trim the irrelevant edges without breaking the story.

Copy timestamps from the transcript's own time markers. Do not invent times that never appear
in it.

==================================================
REQUIRED FIELDS (EXACTLY 7 - NO MORE, NO FEWER)
===============================================

Every object MUST have:

1. "start_time"     (string)  -> format "HH:MM:SS,mmm"
2. "end_time"       (string)  -> format "HH:MM:SS,mmm"
3. "title"          (string)  -> max 60 characters, {output_language}, click-worthy, true to the clip
4. "description"    (string)  -> max 150 characters, {output_language}, why people will watch it
5. "virality_score" (integer) -> 1-10, a bare number, never a string, using the rubric below
6. "hook_text"      (string)  -> max 15 words, {output_language}
7. "category"       (string)  -> exactly one of: drama, argument, scandal, wild, wtf, funny,
                                 punchline, roast, spicy, emotional, hot_take, other

Forbidden:

* Any extra field
* A "reason" field
* virality_score written as a string
* Any comment or prose outside the JSON

==================================================
SCORING virality_score
======================

__RUBRIC__

==================================================
hook_text RULES
===============

* Max 15 words.
* Casual {output_language}.
* No emoji.
* Name the speaker only if their name appears in the transcript or the video info. Otherwise
  say he, she or they. Never invent a name.
* Must be a quote, a sharp statement or a punchline, never a summary.

==================================================
OUTPUT FORMAT (STRICT)
======================

Return ONLY a JSON array. No markdown, no prose, no comments.

QUOTE CHARACTERS (THE MOST COMMON WAY THIS FAILS):

* Never use a double quote (") inside any field value.
* To quote someone, use single quotes (').
  RIGHT: "hook_text":"He said 'I was completely broke' and laughed"
  WRONG: "hook_text":"He said "I was completely broke" and laughed"
  (the example is in English to show the mechanic; write yours in {output_language})
* One stray double quote breaks the ENTIRE response, not just one clip.
* No line breaks inside string values.

Exact shape:

[{"start_time":"HH:MM:SS,mmm","end_time":"HH:MM:SS,mmm","title":"...","description":"...","virality_score":8,"hook_text":"...","category":"drama"}]

==================================================
SOURCE
======

{video_context}

Transcript:
{transcript}

==================================================
FINAL CHECK BEFORE YOU ANSWER
=============================

1. Exactly {num_clips} objects in the array.
2. Every clip runs 60-120 seconds, measured from the timestamps you wrote, unless a user
   direction asked for an exact range.
3. No two clips overlap, and they are not just consecutive chunks from the opening minutes.
4. title, description and hook_text are in {output_language}.
5. Exactly the 7 required fields, virality_score is a bare integer, category is from the list.
6. No double quote (") inside any field value.
7. Scores follow the rubric and are spread honestly, not all 8s and 9s.

Answer with the JSON array and nothing else.""".replace("__RUBRIC__", VIRALITY_RUBRIC)
