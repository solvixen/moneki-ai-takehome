"""对话历史。"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

MAX_TURNS = 6
MAX_SESSIONS = 500


class SessionStore:
    """按 session_id 分桶的最近几轮对话，够解追问就行。

    历史（缺陷 C1）：旧实现只有一个全局 _turns 列表，session_id 参数
    完全被无视——两个会话互相看到对方的历史，追问必然串线。
    现在每个 session_id（含 None）各自一个队列，互不可见；
    会话总数超限时按最久未活跃淘汰（OrderedDict）。
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS, max_turns: int = MAX_TURNS) -> None:
        self._sessions: "OrderedDict[Optional[str], list[dict]]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_turns = max_turns

    def history(self, session_id: Optional[str]) -> list[dict]:
        with self._lock:
            turns = self._sessions.get(session_id)
            return list(turns) if turns else []

    def append(self, session_id: Optional[str], turn: dict) -> None:
        with self._lock:
            turns = self._sessions.get(session_id)
            if turns is None:
                # 新会话：满了就淘汰最久未活跃的那个（队首）。
                while len(self._sessions) >= self.max_sessions:
                    self._sessions.popitem(last=False)
                turns = []
                self._sessions[session_id] = turns
            else:
                self._sessions.move_to_end(session_id)
            turns.append(turn)
            del turns[: max(0, len(turns) - self.max_turns)]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
