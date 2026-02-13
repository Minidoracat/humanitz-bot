"""RCON 連線管理服務 — async 封裝、自動重連、結構化資料解析。"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from humanitz_bot.rcon_client import RconConnectionError, SourceRCON

logger = logging.getLogger("humanitz_bot.rcon")


@dataclass
class ServerInfo:
    name: str = ""
    player_count: int = 0
    max_players: int = 50
    season: str = ""
    weather: str = ""
    game_time: str = ""
    fps: int = 0
    zombies: int = 0
    humans: int = 0
    animals: int = 0
    player_names: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class PlayerInfo:
    name: str
    steam_id: str
    eos_id: str


@dataclass
class FetchAllResult:
    server_info: ServerInfo | None = None
    players: list[PlayerInfo] = field(default_factory=list)
    chat_raw: str = ""
    online: bool = False
    error: str | None = None


# regex: 玩家名 (Steam64ID_+_|EOS_ID)
_PLAYER_RE = re.compile(r"^(.+?) \((\d+)_\+_\|([a-f0-9]+)\)$")
# AI: Zombies=135  Human=5 Animal=16
_AI_RE = re.compile(r"Zombies=(\d+)\s+Human=(\d+)\s+Animal=(\d+)")


class RconService:
    """RCON 連線管理 — async 封裝、自動重連、批次執行。

    所有 public methods 都是 async，內部透過 asyncio.to_thread
    包裝同步的 SourceRCON 操作。
    """

    def __init__(self, host: str, port: int, password: str) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._client: SourceRCON | None = None
        self._lock = asyncio.Lock()
        self._backoff = [5, 10, 30, 60]
        self._backoff_index = 0

    def _connect_sync(self) -> bool:
        """同步連線 + 認證。不 log 密碼。"""
        client = SourceRCON(self._host, self._port, timeout=10)
        if not client.connect():
            return False
        if not client.authenticate(self._password):
            client.close()
            return False
        self._client = client
        self._backoff_index = 0
        return True

    async def _ensure_connected(self) -> bool:
        """確保連線有效，必要時重連。"""
        if self._client is not None and self._client.is_connected:
            return True

        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        logger.info("嘗試建立 RCON 連線: %s:%d", self._host, self._port)
        connected = await asyncio.to_thread(self._connect_sync)
        if connected:
            logger.info("RCON 連線已建立")
            return True

        backoff = self._backoff[min(self._backoff_index, len(self._backoff) - 1)]
        self._backoff_index += 1
        logger.warning("RCON 連線失敗，下次重試延遲 %ds", backoff)
        return False

    async def execute(self, command: str, read_timeout: float = 3.5) -> str:
        """async 執行單一 RCON 指令。

        Returns:
            指令回應文字。連線失敗時回傳空字串。
        """
        async with self._lock:
            if not await self._ensure_connected():
                return ""
            assert self._client is not None
            try:
                body, _packets = await asyncio.to_thread(
                    self._client.execute_simple, command, read_timeout
                )
                return body
            except (RconConnectionError, OSError) as e:
                logger.warning("RCON 指令執行失敗 (%s): %s", command, e)
                self._client = None
                return ""

    async def fetch_all(self) -> FetchAllResult:
        """批次執行 info + Players + fetchchat，回傳結構化資料。

        在同一個 lock 內依序執行三個指令，避免連線衝突。
        """
        async with self._lock:
            if not await self._ensure_connected():
                return FetchAllResult(online=False, error="RCON 連線失敗")
            assert self._client is not None

            result = FetchAllResult(online=True)
            try:
                info_raw, _ = await asyncio.to_thread(
                    self._client.execute_simple, "info", 3.5
                )
                result.server_info = self._parse_info(info_raw)

                players_raw, _ = await asyncio.to_thread(
                    self._client.execute_simple, "Players", 3.5
                )
                result.players = self._parse_players(players_raw)

                chat_raw, _ = await asyncio.to_thread(
                    self._client.execute_simple, "fetchchat", 3.5
                )
                result.chat_raw = chat_raw

            except (RconConnectionError, OSError) as e:
                logger.warning("fetch_all 執行中斷: %s", e)
                self._client = None
                result.online = False
                result.error = str(e)

            return result

    @staticmethod
    def _parse_info(raw: str) -> ServerInfo:
        """解析 info 指令回應。

        格式（\\r\\n 分隔）::

            Name: [TW] PVE Minidoracat HumanitZ Server #1
            8 connected.
            Season: Summer
            Weather: Overcast
            Time: 5:31
            AI: Zombies=135  Human=5 Animal=16
            FPS: 60
            Players:
            konz
            kevin052926
        """
        info = ServerInfo(raw=raw)
        lines = raw.replace("\r\n", "\n").split("\n")

        in_players_section = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if in_players_section:
                info.player_names.append(stripped)
                continue

            if stripped.startswith("Name: "):
                info.name = stripped[6:]
            elif stripped == "Players:":
                in_players_section = True
            elif stripped.startswith("Season: "):
                info.season = stripped[8:]
            elif stripped.startswith("Weather: "):
                info.weather = stripped[9:]
            elif stripped.startswith("Time: "):
                info.game_time = stripped[6:]
            elif stripped.startswith("FPS: "):
                try:
                    info.fps = int(stripped[5:])
                except ValueError:
                    pass
            elif "connected." in stripped:
                m = re.match(r"(\d+)\s+connected\.", stripped)
                if m:
                    info.player_count = int(m.group(1))
            elif stripped.startswith("AI:"):
                m = _AI_RE.search(stripped)
                if m:
                    info.zombies = int(m.group(1))
                    info.humans = int(m.group(2))
                    info.animals = int(m.group(3))

        return info

    @staticmethod
    def _parse_players(raw: str) -> list[PlayerInfo]:
        """解析 Players 指令回應。

        每行格式: ``玩家名 (Steam64ID_+_|EOS_ProductUserID)``
        """
        players: list[PlayerInfo] = []
        for line in raw.replace("\r\n", "\n").split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            m = _PLAYER_RE.match(stripped)
            if m:
                players.append(
                    PlayerInfo(
                        name=m.group(1),
                        steam_id=m.group(2),
                        eos_id=m.group(3),
                    )
                )
        return players

    async def close(self) -> None:
        """關閉 RCON 連線。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            logger.info("RconService 連線已關閉")
