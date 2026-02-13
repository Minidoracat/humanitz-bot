"""
Source RCON 協議客戶端 — 針對 HumanitZ 伺服器最佳化。

HumanitZ RCON 特性：
- 遵循 Source RCON 協議（Valve Developer Wiki）
- 所有回應封包的 request_id 固定為 0（非標準）
- 不回應空指令（end-marker 技巧無法使用）
- 回應延遲約 3 秒/指令
- 認證時先回 RESPONSE_VALUE (type=0) 再回 AUTH_RESPONSE (type=2)
"""

from __future__ import annotations

import logging
import socket
import struct

logger = logging.getLogger("humanitz_bot.rcon_client")

# Source RCON 封包類型 (Valve Developer Wiki)
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(Exception):
    """RCON 操作錯誤的基礎例外。"""


class RconConnectionError(RconError):
    """RCON 連線相關錯誤。"""


class RconAuthError(RconError):
    """RCON 認證失敗。"""


class SourceRCON:
    """Source RCON 協議客戶端，針對 HumanitZ 伺服器特性最佳化。

    不使用 end-marker 技巧，改用短超時讀取回應封包。

    Usage::

        rcon = SourceRCON("127.0.0.1", 8888)
        rcon.connect()
        rcon.authenticate(password)
        response, packets = rcon.execute_simple("info")
        rcon.close()
    """

    def __init__(self, host: str, port: int, timeout: float = 10) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._request_id = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """檢查 socket 是否有效連線。"""
        if self._sock is None:
            return False
        try:
            # 用 getpeername 檢測 socket 是否仍然連接
            self._sock.getpeername()
            return True
        except (OSError, AttributeError):
            return False

    # ------------------------------------------------------------------
    # 封包構建與讀取
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _build_packet(self, request_id: int, packet_type: int, body: str) -> bytes:
        """構建 Source RCON 封包。

        Wire format: [size:i32][id:i32][type:i32][body\\x00][pad\\x00]
        """
        body_bytes = body.encode("utf-8") + b"\x00\x00"
        size = 4 + 4 + len(body_bytes)  # id + type + body_with_nulls
        return struct.pack("<iii", size, request_id, packet_type) + body_bytes

    def _recv_exact(self, n: int) -> bytes:
        """精確讀取 n bytes，確保完整接收。"""
        if self._sock is None:
            raise RconConnectionError("未建立連線")
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise RconConnectionError("連線已關閉")
            data += chunk
        return data

    def _read_packet_raw(self) -> tuple[int, int, int, bytes, str]:
        """讀取並解析一個 RCON 封包。

        Returns:
            (size, request_id, packet_type, raw_body_bytes, body_str)
        """
        raw_size = self._recv_exact(4)
        (size,) = struct.unpack("<i", raw_size)

        raw_data = self._recv_exact(size)
        request_id = struct.unpack("<i", raw_data[0:4])[0]
        packet_type = struct.unpack("<i", raw_data[4:8])[0]
        raw_body = raw_data[8:]
        body = raw_body.rstrip(b"\x00").decode("utf-8", errors="replace")

        return size, request_id, packet_type, raw_body, body

    # ------------------------------------------------------------------
    # 連線管理
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """建立 TCP 連線到 RCON 伺服器。

        Returns:
            True 連線成功，False 連線失敗。
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        try:
            self._sock.connect((self.host, self.port))
            logger.info("RCON TCP 連線成功: %s:%d", self.host, self.port)
            return True
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            logger.error("RCON 連線失敗: %s:%d - %s", self.host, self.port, e)
            self._sock = None
            return False

    def authenticate(self, password: str) -> bool:
        """執行 RCON 認證。

        HumanitZ 認證流程：
        1. 送出 AUTH 封包
        2. 伺服器先回 RESPONSE_VALUE (type=0, body="None")
        3. 再回 AUTH_RESPONSE (type=2)

        嘗試讀取最多 3 個封包以處理非標準行為。

        Args:
            password: RCON 密碼（不會被 log）。

        Returns:
            True 認證成功，False 認證失敗。
        """
        if self._sock is None:
            raise RconConnectionError("未建立連線，請先呼叫 connect()")

        req_id = self._next_id()
        packet = self._build_packet(req_id, SERVERDATA_AUTH, password)
        self._sock.sendall(packet)
        logger.debug("送出 AUTH 封包, id=%d", req_id)

        for i in range(3):
            try:
                size, resp_id, resp_type, _raw_body, body = self._read_packet_raw()
                logger.debug(
                    "AUTH 回應 #%d: size=%d id=%d type=%d body=%r",
                    i + 1,
                    size,
                    resp_id,
                    resp_type,
                    body,
                )
                if resp_type == SERVERDATA_AUTH_RESPONSE:
                    if resp_id == req_id:
                        logger.info("RCON 認證成功")
                        return True
                    # HumanitZ 回傳 id=0，但 AUTH_RESPONSE type=2 仍代表成功
                    # 只要收到 AUTH_RESPONSE 且非 -1 就算成功
                    if resp_id != -1:
                        logger.info("RCON 認證成功 (resp_id=%d)", resp_id)
                        return True
                    logger.warning("RCON 認證失敗: resp_id=%d", resp_id)
                    return False
            except socket.timeout:
                logger.debug("AUTH 讀取 #%d 超時", i + 1)
                break

        logger.error("RCON 認證失敗: 未收到 AUTH_RESPONSE")
        return False

    def execute_simple(
        self, command: str, read_timeout: float = 3.5
    ) -> tuple[str, list[dict]]:
        """送出指令並用短超時讀取所有回應封包。

        不使用 end-marker 技巧。HumanitZ 不回應空指令，
        因此改用短超時來判斷回應結束。

        Args:
            command: 要執行的 RCON 指令。
            read_timeout: 讀取回應的超時秒數（預設 3.5 秒）。

        Returns:
            (combined_body, packet_debug_info) — 合併回應文字與封包偵錯資訊。
        """
        if self._sock is None:
            raise RconConnectionError("未建立連線")

        cmd_id = self._next_id()
        self._sock.sendall(self._build_packet(cmd_id, SERVERDATA_EXECCOMMAND, command))
        logger.debug("送出指令: %r (id=%d)", command, cmd_id)

        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(read_timeout)

        packets: list[dict] = []
        body_parts: list[str] = []
        try:
            while True:
                size, resp_id, resp_type, raw_body, body = self._read_packet_raw()
                packets.append(
                    {
                        "size": size,
                        "id": resp_id,
                        "type": resp_type,
                        "body": body,
                        "body_hex": raw_body.hex(),
                        "body_len": len(body),
                    }
                )
                body_parts.append(body)
        except socket.timeout:
            pass
        except Exception as e:
            packets.append({"error": str(e)})
            logger.warning("讀取回應時發生錯誤: %s", e)
        finally:
            self._sock.settimeout(old_timeout)

        combined = "".join(body_parts)
        logger.debug(
            "指令 %r: 收到 %d 個封包, 回應長度=%d",
            command,
            len(packets),
            len(combined),
        )
        return combined, packets

    def close(self) -> None:
        """關閉 RCON 連線。"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            logger.info("RCON 連線已關閉")
