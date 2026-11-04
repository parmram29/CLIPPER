# Upstream

CLIPPER started as a copy of [op7418/Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill)
at commit `f31f077ee0905c95a510a6f34bbd0c3c85b15129`, MIT-licensed. The original
`LICENSE` file is kept unchanged, as the license requires.

Files copied unmodified from upstream: `scripts/`, `references/`, `templates/`,
`SKILL.md`, `README*.md`, `TECHNICAL_NOTES.md`, `FIXES_AND_IMPROVEMENTS.md`,
`install_as_skill.sh`, `.env.example`, `.github/`.

Changes made in this repo are listed in the git history.

## Notes on upstream behaviour

- The scripts contain **no LLM calls**. Chapter analysis and subtitle translation
  are done by Claude Code itself while it runs the skill (see the comments in
  `scripts/translate_subtitles.py`). `translate_subtitles.py` only prepares data and
  returns a `[待翻译]` placeholder.
- `install_as_skill.sh` copies with `cp -r "$SCRIPT_DIR"/*`, which skips dotfiles, so
  `.env.example` is not copied and no `.env` gets created.
- `install_as_skill.sh` runs a bare `pip3 install`, which fails on Debian/Ubuntu
  Python builds that block system-wide installs. Use `setup.sh` (virtualenv) instead.
