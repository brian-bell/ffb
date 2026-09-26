// Read-only capture of one Yahoo league's current-week state.
//
// Run with the browser's JavaScript tool in a signed-in tab on any
// football.fantasysports.yahoo.com page. It only issues same-origin GETs for
// the settings, teams, and roster pages and never clicks or submits anything.
// The result is stored on window.__ffbCapture; export it with export-chunk.js.
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

  // Rosters: one "slot|player_id|team|positions|name" line per rostered player.
  const rosters = {};
  let week = WEEK;
  for (const [id] of teams) {
    const { doc } = await page(`/f1/${LEAGUE_ID}/${id}/team${week ? `?week=${week}` : ""}`);
    if (!week) week = Number((doc.body.innerText.match(/Week (\d+)/) || [])[1]);
    rosters[id] = [...doc.querySelectorAll("table tbody tr")]
      .filter((tr) => tr.querySelectorAll("td").length >= 6 && !/\(Empty\)/.test(tr.innerText))
      .map((tr) => {
        const slot = cellText(tr.querySelector("td"));
        const ids = [...new Set([...tr.innerHTML.matchAll(/pid=(\d+)|playerid="(\d+)"/g)].map((m) => m[1] || m[2]))];
        const name = tr.querySelector("a.name")?.innerText.trim();
        const teamPos = tr.innerText.match(/\n\s*([A-Za-z]{2,3}) - ([A-Z,/]+)\s*\n/);
        if (ids.length !== 1 || !name || !teamPos) {
          throw new Error(`team ${id}: unparsed roster row in slot ${slot} (${ids.length} ids, name=${name})`);
        }
        return [slot, ids[0], teamPos[1], teamPos[2], name].join("|");
      });
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
  };
  const text = JSON.stringify(window.__ffbCapture);
  ({
    league_name: leagueName,
    game_keys: gameKeys,
    week,
    teams: teams.length,
    players: Object.values(rosters).reduce((n, r) => n + r.length, 0),
    scoring_rows: scoring.length,
    chars: text.length,
    chunks: Math.ceil(text.length / 1000),
  });
}
