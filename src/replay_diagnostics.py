"""公开回放失败的最小本地记录，不保存异常正文或认证资料。

stage=失败阶段，reason=稳定原因，HTTP=官网响应状态码。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading


DIAGNOSTIC_FILENAME = "对局分析诊断.jsonl"
_LOCK = threading.Lock()
_MESSAGES = {
    "permission": "官网不允许获取这份公开回放。",
    "unavailable": "官网暂未提供可获取的本场回放。",
    "rate_limited": "官网请求频率受限。",
    "network_timeout": "获取公开回放超时。",
    "network_error": "获取公开回放时连接失败。",
    "storage_error": "公开回放无法保存到本地。",
    "incomplete_download": "公开回放未下载完整。",
    "invalid_content": "官网返回的内容不是有效回放。",
    "invalid_response": "官网返回的资料未通过核验。",
    "redirect_blocked": "公开回放跳转地址未通过核验。",
    "http_error": "官网回放请求失败。",
    "service_error": "官网回放服务异常。",
    "invalid_input": "公开回放的玩家或对局编号无效。",
    "download_error": "获取公开回放失败。",
    "parse_error": "公开回放下载完成，但解析失败。",
}


def _public_id(value):
    """只保留数字公开编号，避免把昵称或其它输入当作身份记录。"""
    text = str(value)
    return int(text) if re.fullmatch(r"[1-9][0-9]{0,11}", text) else None


def record_public_replay_failure(data_dir, profile_id, game_id, stage, error):
    """尽力保留最近100条；自身写入失败返回False，不覆盖原来的失败。"""
    temporary = None
    try:
        if stage not in {"download", "parse"}:
            return False
        reason = "parse_error" if stage == "parse" else getattr(error, "reason", None)
        if reason not in _MESSAGES:
            reason = "download_error"
        status = getattr(error, "http_status", None)
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "game_id": _public_id(game_id), "profile_id": _public_id(profile_id),
            "stage": stage, "reason": reason, "http_status": status,
            # 不使用str(error)：异常可能包含URL、认证内容、堆栈或本机路径。
            "message": _MESSAGES[reason],
        }
        path = Path(data_dir) / DIAGNOSTIC_FILENAME
        with _LOCK:
            rows = []
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
            rows = rows[-99:] + [record]
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".对局分析诊断-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(temporary, path)
            temporary = None
        return True
    except Exception:
        # 仅忽略诊断器自身的失败；调用者仍继续展示或抛出原下载/解析错误。
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
