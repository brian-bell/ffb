// Read-only capture of one Yahoo league's current-week state.
//
// Run with the browser's JavaScript tool in a signed-in tab on any
// football.fantasysports.yahoo.com page. It only issues same-origin GETs for
// the settings, teams, and roster pages and never clicks or submits anything.
// The result is stored on window.__ffbCapture; export it with export-chunk.js.
// The summary's bytes and sha256 go to build_bundle.py to check the export.
// Emails, cookies, URLs, and raw HTML are never copied into the capture.
// The block scope lets the script run again in the same tab.
{
  const LEAGUE_ID = "928421";
  const WEEK = null; // null = the week Yahoo shows as current; set a number to override

  async function page(path) {
    const res = await fetch(path, { credentials: "include" });
    if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
    const html = await res.text();
    return { html, doc: new DOMParser().parseFromString(html, "text/html") };
  }

  const cellText = (el) => el.innerText.replace(/\s+/g, " ").trim();
  const SLOTS = new Set(["QB", "WR", "RB", "TE", "W/T", "W/R/T", "DEF", "BN", "IR", "IL"]);
  // Yahoo D/ST player ids (ffb.config.YAHOO_DEFENSE_PLAYER_IDS): 100000 + Yahoo's
  // NFL team id. 100014 Rams, 100024 Chargers, 100033 Ravens.
  // 31 and 32 are not teams. A noisy row for one of these ids is still a defense.
  const YAHOO_DEFENSE_OFFSETS = new Set([
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 33, 34,
  ]);
  const isYahooDefenseId = (playerId) => {
    const n = Number(playerId);
    return Number.isInteger(n) && YAHOO_DEFENSE_OFFSETS.has(n - 100000);
  };
  const teamPosition = (text) => {
    const matches = [...text.matchAll(/\b([A-Za-z]{2,3})\s*-\s*([A-Za-z/]+)/g)];
    return matches.find((m) => /^(DEF|DST)$/i.test(m[2])) || matches[0] || null;
  };

  // League name from the league home page title ("MCFFL | Fantasy Football | ...").
  const home = await page(`/f1/${LEAGUE_ID}`);
  const leagueName = home.doc.title.split(" | ")[0].trim() || null;

  // Settings: roster positions and the league's scoring column. Scoring rows
  // follow section headers whose value cell reads "League Value" (Offense,
  // Defense/Special Teams, Kickers); each rule is [section, label, value].
  const settings = await page(`/f1/${LEAGUE_ID}/settings`);
  const settingRows = [...settings.doc.querySelectorAll("table tr")].map((tr) =>
    [...tr.querySelectorAll("th,td")].map(cellText),
  );
  const rosterPositions = settingRows.find((r) => /^Roster Positions/.test(r[0]))?.[1];
  const scoring = [];
  let section = null;
  for (const r of settingRows) {
    if (r[1] === "League Value") section = r[0];
    else if (section && r.length >= 2 && r[0] && r[1]) {
      scoring.push([section, r[0].replace(/\s*Yahoo Default$/, ""), r[1]]);
    }
  }
  const gameKeys = [
    ...new Set(
      [...settings.html.matchAll(new RegExp(`(\\d+)\\.l\\.${LEAGUE_ID}\\b`, "g"))].map((m) => m[1]),
    ),
  ];

  // Teams: id, team name, manager nickname. The email column is never read.
  const teamsPage = await page(`/f1/${LEAGUE_ID}/teams`);
  const teams = [...teamsPage.doc.querySelectorAll("table tbody tr")]
    .map((tr) => {
      const link = [...tr.querySelectorAll(`a[href*="/f1/${LEAGUE_ID}/"]`)].find(
        (a) => /\/\d+\/?$/.test(a.getAttribute("href")) && cellText(a),
      );
      if (!link) return null;
      const id = link.getAttribute("href").match(/(\d+)\/?$/)[1];
      const tds = [...tr.querySelectorAll("td")];
      const at = tds.findIndex((td) => td.contains(link));
      const manager = tds[at + 1] ? cellText(tds[at + 1]).replace(/\s*(Co-)?Commissioner$/, "") : null;
      return [id, cellText(link), manager];
    })
    .filter(Boolean)
    .sort((a, b) => Number(a[0]) - Number(b[0]));

  // Rosters: one "slot|player_id|team|positions|name" line per rostered player,
  // and the slot label of every rendered roster row, filled or "(Empty)", so the
  // builder can match the rendered slots against Roster Positions and catch a
  // table cut off partway. Only the statTable* roster tables are read; the
  // position legend on the same page also starts rows with slot labels.
  const rosters = {};
  const slots = {};
  let week = WEEK;
  for (const [id] of teams) {
    const { doc } = await page(`/f1/${LEAGUE_ID}/${id}/team${week ? `?week=${week}` : ""}`);
    if (!week) week = Number((doc.body.innerText.match(/Week (\d+)/) || [])[1]);
    rosters[id] = [];
    slots[id] = [];
    for (const tr of doc.querySelectorAll('table[id^="statTable"] tbody tr')) {
      const tds = tr.querySelectorAll("td");
      const slot = tds[0] ? cellText(tds[0]) : "";
      if (!SLOTS.has(slot)) throw new Error(`team ${id}: roster row without a slot label (${slot || "no cells"})`);
      slots[id].push(slot);
      if (/\(Empty\)/.test(tr.innerText)) continue;
      // The rostered player is the name link. Notes and tooltips in the same
      // row mention other pids; counting those made a defense row fail closed.
      const nameEl = tr.querySelector("a.name");
      const name = nameEl ? nameEl.innerText.trim() : "";
      const hrefPid = ((nameEl && nameEl.getAttribute("href")) || "").match(/[?&]pid=(\d+)/);
      const namedId = (nameEl && nameEl.getAttribute("data-ys-playerid")) || (hrefPid && hrefPid[1]);
      const ids = namedId
        ? [namedId]
        : [...new Set([...tr.innerHTML.matchAll(/pid=(\d+)|playerid="(\d+)"/g)].map((m) => m[1] || m[2]))];
      const nameBlock = tr.querySelector(".ysf-player-name");
      const teamPos = teamPosition(nameBlock ? nameBlock.innerText : tr.innerText);
      if (ids.length !== 1 || !name) {
        throw new Error(`team ${id}: unparsed roster row in slot ${slot} (${ids.length} ids, name=${name})`);
      }
      if (isYahooDefenseId(ids[0])) {
        // Keep a real "LAR - DEF" team. A skill-shaped or missing line leaves
        // the team blank so the builder resolves the id (Rams, not a false WR).
        const pos = teamPos ? teamPos[2].toUpperCase() : "";
        const team = pos === "DEF" || pos === "DST" ? teamPos[1] : "";
        rosters[id].push([slot, ids[0], team, "DEF", name].join("|"));
        continue;
      }
      if (tds.length < 6 || !teamPos) {
        throw new Error(`team ${id}: unparsed roster row in slot ${slot} (${ids.length} ids, name=${name})`);
      }
      rosters[id].push([slot, ids[0], teamPos[1], teamPos[2], name].join("|"));
    }
  }

  window.__ffbCapture = {
    league_id: LEAGUE_ID,
    game_keys: gameKeys,
    league_name: leagueName,
    week,
    roster_positions: rosterPositions,
    scoring,
    teams,
    rosters,
    slots,
  };
  // The builder refuses an export whose reassembled UTF-8 bytes do not match
  // this hash and length, so a dropped or doubled character fails closed. The
  // extension blocks hex runs of 20+ characters as encoded data, so report a
  // 16-character SHA-256 prefix; that is ample for catching transcription slips.
  const text = JSON.stringify(window.__ffbCapture);
  const bytes = new TextEncoder().encode(text);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  // Pieces of at most 900 UTF-16 code units for export-chunk.js. A piece never
  // ends on a high surrogate, so an emoji is never split into lone halves.
  const pieces = [];
  for (let at = 0; at < text.length; ) {
    let end = Math.min(at + 900, text.length);
    if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1])) end -= 1;
    pieces.push(text.slice(at, end));
    at = end;
  }
  window.__ffbExport = pieces;
  ({
    league_name: leagueName,
    game_keys: gameKeys,
    week,
    teams: teams.length,
    players: Object.values(rosters).reduce((n, r) => n + r.length, 0),
    slots: Object.values(slots).reduce((n, r) => n + r.length, 0),
    scoring_rows: scoring.length,
    bytes: bytes.length,
    sha256: [...digest.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join(""),
    parts: Math.ceil(pieces.length / 20), // export-chunk.js calls
  });
}
