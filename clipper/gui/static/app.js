/* CLIPPER app. Plain JavaScript, no libraries. Everything shown from the video or the AI goes in as
   text (never as HTML), so a hostile video title can't run anything in the page. */
(() => {
  "use strict";

  const view = document.getElementById("view");
  const busyBar = document.getElementById("busy");
  const setupDot = document.getElementById("setup-dot");
  const toastEl = document.getElementById("toast");
  const OPTIONS_KEY = "clipper.options";
  const DEFAULT_TOP = 3;

  const STYLES = [
    ["drama", "Drama & fights", "Arguments, call-outs, confessions, walk-outs. Most clips must be real drama."],
    ["wild", "Funny & wild", "Reactions, chaos, fails and punchlines. No drama rule."],
    ["podcast", "Podcasts & interviews", "Strong takes and stories from long conversations."],
  ];

  let info = null; // the latest /api/state
  let routeToken = 0;
  let dispose = null;

  // ---------------------------------------------------------------------------------------------
  // small helpers

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  async function request(method, path, body) {
    const init = { method, credentials: "same-origin", headers: { "X-Requested-With": "clipper" } };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (e) {
      throw new ApiError(0, "I can't reach CLIPPER. Is its Terminal window still open?");
    }
    let data = null;
    try {
      data = await response.json();
    } catch (e) {
      /* not JSON */
    }
    if (!response.ok) throw new ApiError(response.status, (data && data.error) || `Something went wrong (${response.status}).`);
    return data;
  }
  const api = { get: (path) => request("GET", path), post: (path, body) => request("POST", path, body || {}) };

  let toastTimer = null;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  async function copyText(text, message) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const area = h("textarea", { class: "offscreen", "aria-hidden": "true" });
      area.value = text;
      document.body.append(area);
      area.select();
      try {
        document.execCommand("copy");
      } catch (e2) {
        /* nothing else to try */
      }
      area.remove();
    }
    toast(message || "Copied");
  }

  function loadOptions() {
    try {
      return JSON.parse(localStorage.getItem(OPTIONS_KEY)) || {};
    } catch (e) {
      return {};
    }
  }
  function saveOptions(options) {
    try {
      localStorage.setItem(OPTIONS_KEY, JSON.stringify(options));
    } catch (e) {
      /* private mode: just don't remember */
    }
  }

  function fmtDate(iso) {
    const date = new Date(iso);
    if (!iso || Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  function trendSummary(t) {
    if (!t) return "";
    if (!t.enabled) return "Live trends are switched off (CLIPPER_LIVE_TRENDS=0 in the .env file).";
    if (t.updated === null) return "Not read yet. CLIPPER reads what's trending by itself the first time you make clips, then every few hours.";
    const hours = t.age_hours;
    const age = hours < 1 ? "just now" : hours < 48 ? `${Math.round(hours)} hours ago` : `${Math.round(hours / 24)} days ago`;
    return `Read ${age} from ${t.sources} ${t.sources === 1 ? "source" : "sources"}.`;
  }
  const isYouTube = (url) => typeof url === "string" && /^https:\/\/www\.youtube\.com\//.test(url);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---------------------------------------------------------------------------------------------
  // the strip at the top: setup warning dot, "busy" banner

  async function refreshChrome() {
    try {
      info = await api.get("/api/state");
    } catch (e) {
      busyBar.hidden = false;
      busyBar.textContent = e.message;
      return;
    }
    const problem = !info.ai.configured || !info.cookies.found;
    setupDot.hidden = !problem;
    setupDot.className = "dot" + (!info.ai.configured ? " bad" : "");
    if (info.busy && !location.hash.startsWith("#/job/")) {
      busyBar.hidden = false;
      busyBar.replaceChildren("CLIPPER is working on a video. ", h("a", { href: `#/job/${info.busy}` }, "See how it's going"));
    } else {
      busyBar.hidden = true;
    }
  }

  function markNav(hash) {
    const key = hash.startsWith("#/setup") ? "setup" : hash.startsWith("#/session") ? "sessions" : hash === "#/" || hash === "#" || hash.startsWith("#/job") ? "home" : "";
    for (const link of document.querySelectorAll("[data-nav]")) {
      if (link.dataset.nav === key) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  // ---------------------------------------------------------------------------------------------
  // home: paste a link

  function sessionCard(s) {
    const title = s.title || s.url || "Untitled video";
    return h(
      "a",
      { class: "session-card", href: `#/session/${s.id}` },
      h("h3", {}, title),
      h(
        "div",
        { class: "chips" },
        s.channel ? h("span", { class: "chip plain" }, s.channel) : null,
        h("span", { class: "chip plain" }, fmtDate(s.created)),
        h("span", { class: "chip" }, plural(s.picks, "moment")),
        s.made ? h("span", { class: "chip good" }, `${plural(s.made, "clip")} made`) : h("span", { class: "chip warn" }, "no clips made yet"),
      ),
    );
  }

  async function homeView(isCurrent) {
    const saved = loadOptions();
    const chosenStyle = STYLES.some(([k]) => k === saved.style) ? saved.style : "drama";

    const urlInput = h("input", {
      type: "url", id: "url", class: "bigfield", placeholder: "Paste a YouTube link here", autocomplete: "off",
      spellcheck: "false", "aria-label": "YouTube link", value: saved.lastUrl || "",
    });
    const errorText = h("p", { class: "error-text", role: "alert", hidden: true });
    const goButton = h("button", { class: "btn primary", type: "submit" }, "Find clips");

    const styleCards = STYLES.map(([key, title, desc]) =>
      h("label", { class: "style-card" },
        h("input", { type: "radio", name: "style", value: key, checked: key === chosenStyle }),
        h("strong", {}, title), h("span", {}, desc)),
    );
    const topSelect = h("select", { id: "top", "aria-label": "How many clips" },
      [1, 2, 3, 4, 5, 6, 8, 10].map((n) => h("option", { value: n, selected: n === (saved.top || DEFAULT_TOP) }, String(n))));
    const chooseBox = h("input", { type: "checkbox", id: "choose", checked: !!saved.pickMyself });
    const nicheInput = h("input", { type: "text", id: "niche", placeholder: (info && info.niche) || "e.g. reality TV drama", value: saved.niche || "", maxlength: "100" });
    const directionInput = h("input", { type: "text", id: "direction", placeholder: "e.g. skip the intro, focus on the argument", value: saved.direction || "", maxlength: "500" });
    const captionsBox = h("input", { type: "checkbox", id: "captions", checked: saved.captions !== false });
    const hookBox = h("input", { type: "checkbox", id: "hook", checked: saved.hook !== false });
    const exportBox = h("input", { type: "checkbox", id: "export", checked: saved.export !== false });
    const backgroundSelect = h("select", { id: "background", "aria-label": "Background" },
      [["blurred", "Blurred copy of the video"], ["black", "Plain black"]].map(([v, t]) => h("option", { value: v, selected: v === (saved.background || "blurred") }, t)));

    function showError(message) {
      errorText.textContent = message;
      errorText.hidden = !message;
    }

    async function submit(event) {
      event.preventDefault();
      showError("");
      const style = (view.querySelector("input[name=style]:checked") || {}).value || "drama";
      const options = {
        lastUrl: urlInput.value.trim(), style, top: Number(topSelect.value), pickMyself: chooseBox.checked,
        niche: nicheInput.value.trim(), direction: directionInput.value.trim(), captions: captionsBox.checked,
        hook: hookBox.checked, export: exportBox.checked, background: backgroundSelect.value,
      };
      if (!options.lastUrl) return showError("Paste a YouTube link first.");
      goButton.disabled = true;
      goButton.textContent = "Starting…";
      try {
        const { id } = await api.post("/api/jobs", {
          kind: options.pickMyself ? "find" : "run", url: options.lastUrl, style, top: options.top, niche: options.niche,
          direction: options.direction, captions: options.captions, hook: options.hook, export: options.export, background: options.background,
        });
        saveOptions(options);
        location.hash = `#/job/${id}`;
      } catch (e) {
        showError(e.message);
        goButton.disabled = false;
        goButton.textContent = "Find clips";
      }
    }

    const notices = [];
    if (info && (!info.ai.configured || !info.cookies.found)) {
      notices.push(
        h("div", { class: "notice warn" }, h("strong", {}, "Almost ready"),
          !info.ai.configured ? "CLIPPER needs your AI key. " : "CLIPPER needs your YouTube cookies file. ",
          h("a", { href: "#/setup" }, "Open Setup to fix it")),
      );
    }

    const form = h("form", { class: "card", onsubmit: submit, novalidate: true },
      h("div", { class: "linkrow" }, urlInput, goButton),
      errorText,
      h("div", { class: "styles", role: "radiogroup", "aria-label": "What kind of clips" }, styleCards),
      h("div", { class: "field-row" },
        h("div", {}, h("label", { class: "field", for: "top" }, "How many clips do you want?"), topSelect),
        h("div", {}, h("label", { class: "field", for: "niche" }, "Your account's topic (for captions)"), nicheInput)),
      h("div", { class: "mt" }, h("label", { class: "check" }, chooseBox, h("span", {}, "Let me choose the moments myself ", h("span", { class: "muted" }, "(I'll show you everything it found first)")))),
      h("details", { class: "more" }, h("summary", {}, "More options"),
        h("div", { class: "field-row" },
          h("div", {}, h("label", { class: "field", for: "direction" }, "Anything specific to look for?"), directionInput),
          h("div", {}, h("label", { class: "field", for: "background" }, "Behind the video"), backgroundSelect)),
        h("div", { class: "field-row" },
          h("label", { class: "check" }, captionsBox, h("span", {}, "Burn captions into the video")),
          h("label", { class: "check" }, hookBox, h("span", {}, "Show the hook text at the start")),
          h("label", { class: "check" }, exportBox, h("span", {}, "Also copy clips to a Desktop folder (for AirDrop)")))),
    );

    const recent = h("section", { "aria-label": "Recent" });
    const trendNote = info && info.trends
      ? h("p", { class: "small muted" }, "Hashtags use what's trending right now, read on its own. ", trendSummary(info.trends), " ", h("a", { href: "#/setup" }, "Details"))
      : null;
    const page = h("div", {},
      h("div", { class: "hero" },
        h("h1", {}, "Turn a YouTube video into ", h("span", { class: "grad" }, "TikTok clips")),
        h("p", {}, "Paste a link. CLIPPER finds the best moments, cuts them vertical, adds captions, and writes the titles and hashtags.")),
      notices, form, trendNote, recent);

    if (!isCurrent()) return;
    view.replaceChildren(page);
    urlInput.focus();

    api.get("/api/sessions").then(({ sessions }) => {
      if (!isCurrent() || !sessions.length) return;
      recent.replaceChildren(h("h2", {}, "Recent"), h("div", { class: "session-list" }, sessions.slice(0, 4).map(sessionCard)),
        sessions.length > 4 ? h("p", { class: "small" }, h("a", { href: "#/sessions" }, `See all ${sessions.length}`)) : null);
    }).catch(() => {});
  }

  // ---------------------------------------------------------------------------------------------
  // a job in progress

  function jobView(id, isCurrent) {
    let since = 0;
    let timer = null;
    let stopped = false;

    const title = h("h2", {}, "Working on it…");
    const headline = h("div", { class: "headline", "aria-live": "polite" }, "Starting…");
    const fill = h("div", { class: "fill" });
    const bar = h("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": "Progress" }, fill);
    const steps = h("ul", { class: "steps" });
    const live = h("div", { class: "live" });
    const log = h("pre", { class: "log" });
    const details = h("details", { class: "more" }, h("summary", {}, "Show details"), log);
    const cancelButton = h("button", { class: "btn danger small", type: "button", onclick: cancel }, "Cancel");
    const outcome = h("div", {});
    const card = h("div", { class: "card" }, h("div", { class: "actions spread" }, title, cancelButton), headline, bar, steps, live, outcome, details);
    view.replaceChildren(card);

    async function cancel() {
      cancelButton.disabled = true;
      try {
        await api.post(`/api/jobs/${id}/cancel`);
      } catch (e) {
        toast(e.message);
      }
    }

    function drawProgress(snap) {
      const p = snap.progress;
      headline.textContent = p.headline;
      fill.style.width = `${p.percent}%`;
      fill.classList.toggle("pulse", snap.status === "running");
      bar.setAttribute("aria-valuenow", String(p.percent));
      steps.replaceChildren(...p.steps.map((s) =>
        h("li", { class: s.state }, h("span", { class: "mark", "aria-hidden": "true" }, s.state === "done" ? "✓" : s.state === "active" ? "•" : ""), s.label)));
      live.textContent = snap.live || "";
    }

    function drawLog(lines) {
      if (!lines.length) return;
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.append(lines.join("\n") + "\n");
      if (nearBottom) log.scrollTop = log.scrollHeight;
    }

    function finish(snap) {
      cancelButton.hidden = true;
      live.textContent = "";
      if (snap.status === "done") {
        if (snap.kind === "export" && snap.session) {
          api.post("/api/open", { what: "export", session: snap.session }).catch(() => {});
        }
        if (snap.session) {
          location.replace(`#/session/${snap.session}`);
          return;
        }
        title.textContent = "Finished";
        fill.style.width = "100%";
        return;
      }
      if (snap.status === "cancelled") {
        title.textContent = "Stopped";
        headline.textContent = "You stopped it. Nothing was lost: anything already made is kept.";
        outcome.replaceChildren(h("div", { class: "actions" },
          snap.session ? h("a", { class: "btn", href: `#/session/${snap.session}` }, "See what was made") : null,
          h("a", { class: "btn", href: "#/" }, "Back")));
        return;
      }
      title.textContent = "That didn't work";
      headline.textContent = "";
      details.open = true;
      const stuck = steps.querySelector("li.active");
      if (stuck) {
        stuck.classList.replace("active", "failed");
        stuck.querySelector(".mark").textContent = "!";
      }
      outcome.replaceChildren(
        h("div", { class: "notice bad" }, h("strong", {}, snap.error || "Something went wrong."),
          snap.error ? "The details below show exactly what happened." : "The details below show exactly what happened. Copying them to me helps."),
        h("div", { class: "actions" },
          snap.session ? h("a", { class: "btn", href: `#/session/${snap.session}` }, "See what was made") : null,
          h("a", { class: "btn primary", href: "#/" }, "Try again"),
          h("button", { class: "btn", type: "button", onclick: () => copyText(log.textContent, "Details copied") }, "Copy details")));
    }

    async function tick() {
      if (stopped) return;
      let snap;
      try {
        snap = await api.get(`/api/jobs/${id}?since=${since}`);
      } catch (e) {
        if (e.status === 404) {
          title.textContent = "I lost track of that one";
          headline.textContent = "CLIPPER was restarted while it was working. Anything it finished is under My clips.";
          cancelButton.hidden = true;
          outcome.replaceChildren(h("a", { class: "btn", href: "#/sessions" }, "My clips"));
          return;
        }
        timer = setTimeout(tick, 2000);
        return;
      }
      if (stopped || !isCurrent()) return;
      since = snap.next;
      drawLog(snap.lines);
      drawProgress(snap);
      if (snap.status === "running") timer = setTimeout(tick, 1000);
      else finish(snap);
    }

    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }

  // ---------------------------------------------------------------------------------------------
  // lists of sessions

  async function sessionsView(isCurrent) {
    const { sessions } = await api.get("/api/sessions");
    if (!isCurrent()) return;
    view.replaceChildren(h("div", {},
      h("h1", {}, "My clips"),
      sessions.length
        ? h("div", { class: "session-list" }, sessions.map(sessionCard))
        : h("div", { class: "card" }, h("p", {}, "Nothing here yet."), h("a", { class: "btn primary", href: "#/" }, "Make your first clips"))));
  }

  // ---------------------------------------------------------------------------------------------
  // one session: the moments it found and the clips made from them

  function copyRow(label, text, buttonText) {
    return h("div", { class: "copyrow" },
      h("div", { class: "text" }, h("span", { class: "lbl" }, label), text),
      h("button", { class: "btn small", type: "button", onclick: () => copyText(text, `${label} copied`) }, buttonText || "Copy"));
  }

  function socialBlock(pick) {
    const copy = pick.social;
    if (!copy) return h("p", { class: "muted small" }, "No caption written for this clip yet.");
    const tags = copy.hashtags.map((t) => `#${t}`);
    const tagLine = tags.join(" ");
    const variants = copy.variants.length ? copy.variants : copy.caption ? [{ style: "caption", caption: copy.caption }] : [];
    const label = (v, i) => (v.style ? v.style.charAt(0).toUpperCase() + v.style.slice(1) : `Caption ${i + 1}`);
    return h("div", {},
      variants.map((v, i) => copyRow(label(v, i), `${v.caption} ${tagLine}`.trim(), "Copy caption + hashtags")),
      tags.length ? h("div", { class: "copyrow" },
        h("div", { class: "text" }, h("span", { class: "lbl" }, "Hashtags"), h("div", { class: "hashtags" }, copy.hashtags.map((t) => h("span", { class: "chip" + (copy.trend_tags.includes(t) ? " good" : ""), title: copy.trend_tags.includes(t) ? "From the live trend sources" : null }, `#${t}`)))),
        h("button", { class: "btn small", type: "button", onclick: () => copyText(tagLine, "Hashtags copied") }, "Copy hashtags")) : null,
      copy.pinned_comment ? copyRow("Pinned comment", copy.pinned_comment) : null,
      copy.keywords.length ? h("p", { class: "small muted" }, "Search words: ", copy.keywords.join(", ")) : null);
  }

  function pickCard(pick, rank, selection, onSelect) {
    const chips = h("div", { class: "chips" },
      pick.category ? h("span", { class: "chip" }, pick.category) : null,
      pick.drama !== null ? h("span", { class: "chip " + (pick.drama >= 6 ? "good" : "plain"), title: pick.drama_why || "" }, `drama ${pick.drama}/10`) : null,
      pick.virality !== null ? h("span", { class: "chip plain" }, `score ${pick.virality}/10`) : null,
      h("span", { class: "chip plain" }, `${pick.start} – ${pick.end}${pick.seconds ? ` · ${Math.round(pick.seconds)} s` : ""}`));
    const checkbox = h("input", { type: "checkbox", checked: selection.has(pick.number), onchange: (e) => onSelect(pick.number, e.target.checked) });
    const body = h("div", {},
      h("div", { class: "head" },
        h("div", {}, h("h3", {}, h("span", { class: "rank" }, `#${rank}`), pick.social && pick.social.title ? pick.social.title : pick.title),
          pick.hook ? h("div", { class: "hook" }, `“${pick.hook}”`) : null),
        pick.rendered ? h("span", { class: "chip good" }, "made") : h("label", { class: "check" }, checkbox, h("span", {}, "Make this one"))),
      chips,
      pick.rendered ? socialBlock(pick) : h("p", { class: "muted small mt-s" }, pick.description || ""));
    const card = h("article", { class: "card pick" + (pick.rendered ? " has-video" : "") });
    if (pick.rendered) card.append(h("video", { class: "clip", controls: true, preload: "metadata", playsinline: true, src: pick.video, "aria-label": `Clip: ${pick.title}` }));
    card.append(body);
    return card;
  }

  async function sessionView(id, isCurrent) {
    const data = await api.get(`/api/sessions/${id}`);
    if (!isCurrent()) return;
    const made = data.picks.filter((p) => p.rendered).length;
    const selection = new Set(made === 0 ? data.ranking.slice(0, DEFAULT_TOP).map((i) => data.picks[i].number) : []);
    const makeButton = h("button", { class: "btn primary", type: "button" }, "Make clips");

    makeButton.hidden = data.picks.every((p) => p.rendered); // nothing left to make

    function refreshMakeButton() {
      const n = selection.size;
      makeButton.className = "btn" + (n ? " primary" : "");
      makeButton.textContent = n ? `Make ${plural(n, "clip")}` : made ? "Make more: tick moments below" : "Tick the moments you want";
      makeButton.disabled = n === 0 || !!(info && info.busy);
    }
    makeButton.addEventListener("click", async () => {
      makeButton.disabled = true;
      try {
        const { id: jobId } = await api.post("/api/jobs", {
          kind: "process", session: id, picks: [...selection].sort((a, b) => a - b),
          niche: loadOptions().niche || "", captions: loadOptions().captions !== false, hook: loadOptions().hook !== false,
          background: loadOptions().background || "blurred", export: loadOptions().export !== false,
        });
        location.hash = `#/job/${jobId}`;
      } catch (e) {
        toast(e.message);
        refreshMakeButton();
      }
    });
    refreshMakeButton();

    async function openFolder(what) {
      try {
        const result = await api.post("/api/open", { what, session: id });
        if (!result.opened) toast(`It's here: ${result.path}`);
      } catch (e) {
        toast(e.message);
      }
    }
    async function exportNow() {
      try {
        const { id: jobId } = await api.post("/api/jobs", { kind: "export", session: id });
        location.hash = `#/job/${jobId}`;
      } catch (e) {
        toast(e.message);
      }
    }

    const title = data.video.title || data.url || "Your video";
    const drama = data.drama;
    const picksList = h("div", {}, data.ranking.map((index, position) => {
      const pick = data.picks[index];
      return pickCard(pick, position + 1, selection, (number, on) => {
        if (on) selection.add(number);
        else selection.delete(number);
        refreshMakeButton();
      });
    }));

    view.replaceChildren(h("div", {},
      h("p", { class: "small" }, h("a", { href: "#/sessions" }, "← My clips")),
      h("div", { class: "card" },
        h("h1", {}, title),
        h("div", { class: "chips" },
          data.video.channel ? h("span", { class: "chip plain" }, data.video.channel) : null,
          h("span", { class: "chip plain" }, fmtDate(data.created)),
          h("span", { class: "chip" }, plural(data.picks.length, "moment")),
          h("span", { class: "chip " + (made ? "good" : "warn") }, `${plural(made, "clip")} made`),
          drama.judged ? h("span", { class: "chip " + (drama.scenes / Math.max(1, drama.total) >= 0.75 ? "good" : "warn") }, `${drama.scenes} of ${drama.total} are drama scenes`) : null,
          isYouTube(data.url) ? h("a", { class: "small", href: data.url, target: "_blank", rel: "noopener noreferrer" }, "Open on YouTube ↗") : null),
        h("div", { class: "actions mt" },
          makeButton,
          made ? h("button", { class: "btn", type: "button", onclick: () => openFolder("clips") }, "Show clips in Finder") : null,
          made ? h("button", { class: "btn", type: "button", onclick: exportNow }, data.exported ? "Update Desktop folder" : "Copy to Desktop folder") : null,
          data.exported ? h("button", { class: "btn", type: "button", onclick: () => openFolder("export") }, `Open Desktop folder (${data.exported})`) : null)),
      data.picks.length ? picksList : h("div", { class: "card" }, "This session didn't find any moments.")));
  }

  // ---------------------------------------------------------------------------------------------
  // setup check

  async function setupView(isCurrent) {
    const rows = h("div", { class: "rows", "aria-live": "polite" }, h("p", { class: "muted" }, "Checking…"));
    const runButton = h("button", { class: "btn primary", type: "button" }, "Run the check");
    const aiButton = h("button", { class: "btn", type: "button" }, "Test the AI connection");

    async function check(ai) {
      runButton.disabled = aiButton.disabled = true;
      rows.replaceChildren(h("p", { class: "muted" }, ai ? "Asking the AI a tiny question…" : "Checking…"));
      try {
        const result = await api.post("/api/doctor", { ai });
        if (!isCurrent()) return;
        rows.replaceChildren(...(result.rows.length
          ? result.rows.map((r) => h("div", { class: "row " + (r.level === "ok" ? "" : r.level) },
              h("span", { class: "chip badge " + (r.level === "ok" ? "good" : r.level === "warn" ? "warn" : "bad") }, r.level === "ok" ? "OK" : r.level === "warn" ? "Check" : "Fix"),
              h("span", {}, r.message)))
          : [h("pre", { class: "log" }, result.output || "No output.")]));
        await refreshChrome();
      } catch (e) {
        rows.replaceChildren(h("div", { class: "notice bad" }, e.message));
      } finally {
        runButton.disabled = aiButton.disabled = false;
      }
    }
    runButton.addEventListener("click", () => check(false));
    aiButton.addEventListener("click", () => check(true));

    const trendBox = h("div", { class: "rows", "aria-live": "polite" });
    const trendButton = h("button", { class: "btn", type: "button" }, "Check the trend sources");
    function drawTrends(t, lines) {
      const rowsOut = [];
      if (t && t.failure) rowsOut.push(h("div", { class: "row warn" }, h("span", { class: "chip badge warn" }, "Check"), h("span", {}, `The last read had a problem (${t.failure}). It tries again within the hour.`)));
      for (const line of lines || (t && t.notes) || []) {
        const bad = line.includes("FAILED");
        const skipped = line.includes("skipped");
        rowsOut.push(h("div", { class: "row " + (bad ? "fail" : skipped ? "warn" : "") },
          h("span", { class: "chip badge " + (bad ? "bad" : skipped ? "warn" : "good") }, bad ? "Failed" : skipped ? "Skipped" : "OK"),
          h("span", {}, line.replace(": FAILED, ", ": ").replace(/: skipped: /, ": "))));
      }
      if (t && t.hot && t.hot.length) rowsOut.push(h("p", { class: "small" }, h("strong", {}, "Hot and relevant: "), t.hot.join(", ")));
      if (t && t.tags && t.tags.length) rowsOut.push(h("div", { class: "hashtags" }, t.tags.map((tag) => h("span", { class: "chip" }, `#${tag}`))));
      trendBox.replaceChildren(...rowsOut);
    }
    drawTrends(info && info.trends, null);
    trendButton.addEventListener("click", async () => {
      trendButton.disabled = true;
      trendBox.replaceChildren(h("p", { class: "muted" }, "Reading the sources… this takes up to half a minute."));
      try {
        const result = await api.post("/api/trends/check");
        if (!isCurrent()) return;
        drawTrends(result.status, null);
        if (!result.ok) trendBox.prepend(h("div", { class: "notice warn" }, "Nothing could be read. Check your internet connection."));
        await refreshChrome();
      } catch (e) {
        trendBox.replaceChildren(h("div", { class: "notice bad" }, e.message));
      } finally {
        trendButton.disabled = false;
      }
    });
    const trendCard = h("div", { class: "card" },
      h("h2", {}, "Hashtags: what's trending now"),
      h("p", { class: "muted" }, "CLIPPER reads public sources on its own (search trends, Wikipedia, news, social topics, and what people search for about your video) and uses what fits each clip. This is not TikTok's own data: TikTok offers no feed for that. ", trendSummary(info && info.trends)),
      h("div", { class: "actions" }, trendButton), trendBox);

    view.replaceChildren(h("div", {},
      h("h1", {}, "Is everything ready?"),
      h("div", { class: "card" },
        h("div", { class: "actions" }, runButton, aiButton,
          h("button", { class: "btn", type: "button", onclick: () => api.post("/api/open", { what: "project" }).catch((e) => toast(e.message)) }, "Open the CLIPPER folder")),
        rows),
      trendCard,
      h("div", { class: "card help" },
        h("h2", {}, "If something needs fixing"),
        h("h3", {}, "AI key"),
        h("p", {}, "Open the CLIPPER folder (button above), open the file named ", h("code", {}, ".env"), " in any text editor, and put your Alibaba key after ", h("code", {}, "CLIPPER_API_KEY="), ". Save it, then run the check again."),
        h("h3", {}, "YouTube cookies"),
        h("p", {}, "While logged in to YouTube, use the “Get cookies.txt LOCALLY” browser extension to export cookies for youtube.com only. Save the file as ", h("code", {}, "cookies.txt"), " inside the CLIPPER folder."),
        h("h3", {}, "ffmpeg"),
        h("p", {}, "ffmpeg cuts and captions the video. If the check says it's missing, the README's setup section shows how to install it."))));
    check(false);
  }

  // ---------------------------------------------------------------------------------------------
  // router

  const routes = [
    [/^#\/?$/, homeView],
    [/^#\/job\/([A-Za-z0-9_-]+)$/, jobView],
    [/^#\/sessions$/, sessionsView],
    [/^#\/session\/([0-9]{8}_[0-9]{6})$/, sessionView],
    [/^#\/setup$/, setupView],
  ];

  async function route() {
    if (dispose) {
      dispose();
      dispose = null;
    }
    const token = ++routeToken;
    const isCurrent = () => token === routeToken;
    const hash = location.hash || "#/";
    markNav(hash);
    await refreshChrome();
    if (!isCurrent()) return;
    for (const [pattern, handler] of routes) {
      const match = hash.match(pattern);
      if (!match) continue;
      try {
        const result = await handler(...match.slice(1), isCurrent);
        if (isCurrent() && typeof result === "function") dispose = result;
        else if (typeof result === "function") result();
      } catch (e) {
        if (isCurrent()) {
          view.replaceChildren(h("div", { class: "notice bad" }, h("strong", {}, "That didn't load."), e.message, " ", h("a", { href: "#/" }, "Back to the start")));
        }
      }
      if (isCurrent()) {
        window.scrollTo(0, 0);
      }
      return;
    }
    location.hash = "#/";
  }

  window.addEventListener("hashchange", route);
  route();
})();
