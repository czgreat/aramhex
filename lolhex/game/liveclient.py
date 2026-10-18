"""游戏内 Live Client Data API（https://127.0.0.1:2999），只读、无需凭据。

国服是否开放以实测为准；读不到时返回 None，由上层退回其他方式。
"""

from __future__ import annotations

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://127.0.0.1:2999/liveclientdata"


class LiveClient:
    def __init__(self):
        self._s = requests.Session()
        self._s.verify = False

    def _get(self, path: str):
        try:
            r = self._s.get(BASE + path, timeout=1.5)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        return None

    def active_player(self) -> dict | None:
        return self._get("/activeplayer")

    def player_list(self) -> list | None:
        return self._get("/playerlist")

    def game_stats(self) -> dict | None:
        return self._get("/gamestats")

    def snapshot(self) -> dict | None:
        """→ {champion_raw, champion_name, level, game_mode, game_time, enemies}；不可用返回 None。
        enemies：对面英雄的 rawChampionName 列表（认不出自己是哪一边时为空）。2026-10-18 国服海斗实测能读到全场 10 人。"""
        ap = self.active_player()
        if not ap:
            return None
        me = ap.get("riotId") or ap.get("summonerName")
        players = self.player_list() or []
        champ_raw = champ_name = my_team = None
        for p in players:
            if me and me in (p.get("riotId"), p.get("summonerName")):
                champ_raw = p.get("rawChampionName")
                champ_name = p.get("championName")
                my_team = p.get("team")
                break
        enemies = [p.get("rawChampionName") or p.get("championName") for p in players
                   if my_team and p.get("team") and p.get("team") != my_team]
        gs = self.game_stats() or {}
        return {
            "champion_raw": champ_raw,
            "champion_name": champ_name,
            "level": ap.get("level"),
            "game_mode": gs.get("gameMode"),
            "game_time": gs.get("gameTime"),
            "enemies": [e for e in enemies if e],
        }
