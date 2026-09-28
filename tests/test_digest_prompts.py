"""Digest LLM prompts: every named player carries who rosters them."""

import re

from ffb.claude import (
    haiku_system_prompt,
    haiku_user_prompt,
    sonnet_system_prompt,
    sonnet_user_prompt,
)
from ffb.digest import headline_owners

from .test_digest import _week3_report

SONNET_GOLDEN = """\
Week 3 digest for Turkey Supreme.
[roster: mine, on Turkey Supreme]
- Rico Dowdle (RB) flag=none note=
  * Rico Dowdle remains out [names: Rico Dowdle (RB, mine)]
- Kyle Monangai (RB) flag=none note=
  * Kyle Monangai gets more work \
[names: Caleb Williams (QB, rostered by Wigwams); Kyle Monangai (RB, mine)]
[watch: not on the user's team]
- Rookie Wideout (WR) flag=none note=
  * Rookie Wideout drawing waiver buzz [names: Rookie Wideout (WR, free agent)]
[other: league news, not about the user's team unless a name says mine]
- Puka Nacua trending toward Week 4 return [names: Puka Nacua (WR, rostered by WM Diners)]
- Unknown Specialist signs [names: Unknown Specialist (owner unknown)]"""


def _prompts():
    report, mentions, owners = _week3_report()
    tagged = headline_owners(report, mentions, owners)
    return sonnet_user_prompt(report, tagged), haiku_user_prompt(report, tagged)


def test_sonnet_prompt_matches_golden():
    sonnet, _ = _prompts()
    assert sonnet == SONNET_GOLDEN


def test_every_player_headline_carries_ownership_labels():
    sonnet, haiku = _prompts()
    for prompt in (sonnet, haiku):
        headline_lines = [line for line in prompt.splitlines() if line.lstrip().startswith("*")]
        assert headline_lines
        assert all("[names: " in line for line in headline_lines)


def test_rival_players_are_never_labelled_mine():
    sonnet, haiku = _prompts()
    for prompt in (sonnet, haiku):
        for rival in ("Puka Nacua", "Caleb Williams"):
            assert re.search(rf"{rival} \([^)]*mine\)", prompt) is None
    assert "Puka Nacua (WR, rostered by WM Diners)" in sonnet
    assert "Caleb Williams (QB, rostered by Wigwams)" in sonnet
    assert "Caleb Williams (QB, rostered by Wigwams)" in haiku


def test_system_prompts_limit_the_user_team_to_players_marked_mine():
    for system in (haiku_system_prompt(), sonnet_system_prompt()):
        assert "Only players marked mine are on the user's team" in system
    assert "Tuesday" not in sonnet_system_prompt()
    assert "weekly-brief" in sonnet_system_prompt()
