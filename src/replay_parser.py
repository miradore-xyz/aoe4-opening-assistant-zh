"""只读、离线的帝国时代 4 完整录像指令解析。

parse_replay（解析录像）只打开用户选中的普通文件，不连接游戏进程、
不采集网络或键鼠，不启动外部程序，也不修改录像。二进制结构参考
AoE4 World 的 docs/replayData.bt；本模块为独立的 Python 标准库实现。
未知指令保留编号与边界，不将推测写成资源、伤害或单位生产结果。
"""

from collections import Counter
import gzip
import hashlib
import io
import math
from pathlib import Path
import struct
import zipfile
import zlib


class ReplayError(ValueError):
    """文件损坏、格式不支持或无法完整遍历时的中文错误。"""


_MAGIC = b"Relic Chunky\r\n\x1a\x00"
_TICKS_PER_SECOND = 8
_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
_COMMAND_NAMES = {
    3: "queue_unit", 5: "cancel_production", 12: "rally", 14: "entity_ability", 16: "research",
    20: "unload_squads", 22: "attack_forced", 56: "cancel_construction",
    62: "move", 63: "stop", 65: "build", 66: "capture", 67: "attack", 76: "unload", 105: "gather",
    123: "place_building",
    71: "attack_move", 72: "ability", 73: "load", 109: "hold_position", 116: "patrol",
    146: "control", 148: "control", 152: "periodic", 153: "periodic",
}
# 最小原枚举表来自当前安装reflect.sga内reflect.db；不依赖研究目录，也不据旧BT的猜测注释命名。
COMMAND_ENUM_SOURCE = {"build_number": 11308, "archive_member": "engine/archives/reflect.sga:reflect.db",
                       "member_sha256": "972e6873aed83f6efd60c50ac971f5dfcb353e68ee1031e93e9014bfdcd04ca2",
                       "EntityCommandType": {3: "CMD_BuildSquad", 5: "CMD_CancelProduction", 12: "CMD_RallyPoint",
                           14: "CMD_Ability", 16: "CMD_Upgrade", 20: "CMD_UnloadSquads", 22: "CMD_AttackForced", 56: "CMD_CancelConstruction"},
                       "SquadCommandType": {62: "SCMD_Move", 63: "SCMD_Stop", 65: "SCMD_BuildStructure", 66: "SCMD_Capture",
                           67: "SCMD_Attack", 71: "SCMD_AttackMove", 72: "SCMD_Ability", 73: "SCMD_Load", 76: "SCMD_Unload",
                           105: "SCMD_Gather", 109: "SCMD_HoldPosition", 116: "SCMD_AddPatrolPoint"}}
REFERENCE_ENCODING_SOURCE = {"build_number": 11308,
    "artifact_sha256": "5380c577805565817f528af6eac385263413fa6815553f9a31fa62561cb45e8c",
    "write_rva": "0xAE9120", "write_single_be32_rva": "0xAE9030", "read_rva": "0xAE9340",
    "read_base_rva_by_kind": {0: "0xAE976E", 1: "0xAE9766", 2: "0xAE975E", 3: "0xAE9756"},
    "base_by_kind": {0: 1000, 1: 1000005000, 2: 50000, 3: 100000},
    "formula": "kind=(packed>>28)&3; original_id=(packed&0x0FFFFFFF)+base[kind]",
    "byte_order": "单选完整packed为大端u32；多选每项为小端u32；数量头使用原两高位长度分支。"}


class _Reader:
    """带绝对位置和边界检查的 little-endian（小端序）读取器。"""

    def __init__(self, data, start=0, end=None):
        self.data = data
        self.pos = start
        self.end = len(data) if end is None else end

    @property
    def remaining(self):
        return self.end - self.pos

    def take(self, count):
        if count < 0 or count > self.remaining:
            raise ReplayError(f"录像在位置 {self.pos} 处截断或字段长度无效。")
        start = self.pos
        self.pos += count
        return self.data[start:self.pos]

    def skip(self, count):
        self.take(count)

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def u8(self):
        return self.unpack("B")[0]

    def u16(self):
        return self.unpack("H")[0]

    def u32(self):
        return self.unpack("I")[0]

    def string(self, wide=False):
        count = self.u32()
        if count > 1_048_576:
            raise ReplayError("录像中的字符串长度异常。")
        raw = self.take(count * (2 if wide else 1))
        try:
            return raw.decode("utf-16-le" if wide else "utf-8").rstrip("\x00")
        except UnicodeError as exc:
            raise ReplayError("录像中的文字编码无效。") from exc


def _read_chunk(reader, chunks, depth=0):
    """读取 Chunky（分块容器），严格校验父子边界。"""
    if depth > 16:
        raise ReplayError("录像分块嵌套层数异常。")
    kind, ident, version, length = reader.unpack("4s4sII")
    reader.string()
    start = reader.pos
    if kind not in (b"FOLD", b"DATA") or length > reader.remaining:
        raise ReplayError(f"录像在位置 {start} 处包含无效分块。")
    end = start + length
    chunks.append((kind, ident, version, start, end))
    if kind == b"FOLD":
        child = _Reader(reader.data, start, end)
        while child.remaining:
            _read_chunk(child, chunks, depth + 1)
    reader.pos = end


def _chunk_file(reader, chunks):
    if reader.take(16) != _MAGIC:
        raise ReplayError("未识别到帝国时代 4 录像分块标识。")
    version, _platform = reader.unpack("II")
    if version != 4:
        raise ReplayError(f"暂不支持此录像容器版本（{version}）。")
    _read_chunk(reader, chunks)


def _players_from_setup(data, chunk):
    """解析验证过的 DATA 56/58/60 玩家布局；不输出账号或聊天内容。"""
    _, _, version, start, end = chunk
    if version not in (56, 58, 60):
        return None
    reader = _Reader(data, start, end)
    reader.skip(10)
    count = reader.u32()
    if not 1 <= count <= 64:
        raise ReplayError("录像玩家数量无效。")
    players = []
    for _ in range(count):
        human = reader.u8()
        name = reader.string(wide=True)
        team_offset = reader.pos
        team = reader.u32()
        player_id = reader.u32()
        reader.skip(1)
        civilization = reader.string()
        if human not in (0, 1) or player_id > 255:
            raise ReplayError("录像玩家结构与支持的格式不符。")
        reader.skip(8)
        reader.string()
        reader.skip(4 + 4 + 20 + 4 + 4 + 4 + 4 + 5)
        reader.string(wide=True)  # Steam（游戏平台）编号只越过，不保留。
        reader.skip(4 + 4 + 2 + 2 + 1 + 1 + 1 + 15)
        if version == 60:
            # 60 版在扩展资料标记前增加 8 字节；以本机只读副本逐项验证。
            reader.skip(8)
        extra = reader.u32() if version >= 58 else human
        if extra > 3:
            raise ReplayError("录像玩家扩展结构不受支持。")
        if extra:
            reader.skip(69)
            reader.string()
            reader.skip(44)
        if extra > 1:
            reader.skip(69)
            reader.string()
            reader.skip(44)
        if extra > 2:
            # 第三组同样包含变长字符串；117字节只覆盖该字符串为空的情况。
            # 非空时必须消费实际长度，否则后续玩家起点会提前，整组昵称丢失。
            reader.skip(69)
            reader.string()
            reader.skip(44)
        players.append({
            "id": player_id + 1000, "profile_id": None,
            "name": name or f"玩家 {player_id + 1000}",
            "civilization": civilization or None,
            # 保存录像里的原始分队值，不把 0/1 擅自解释为无队或第一队。
            "team": None if team == 0xFFFFFFFF else team,
            "team_evidence": {"value": team, "source": "replay_setup",
                              "setup_version": version, "offset": team_offset}
            if team != 0xFFFFFFFF else None,
        })
    if len({p["id"] for p in players}) != len(players):
        raise ReplayError("录像中出现重复的玩家编号。")
    return players


def _selected_units(reader, references=None):
    """原选择引用的变长数量和带类型u32编码；units保留旧偏移值供兼容。

    original_id（原标识）沿当前原EXE的读取代码恢复，不是由同局集合相交猜出的固定偏移。
    kind（引用类型）各自有基数；不能把建筑、单位及其他引用混在同一编号空间。
    """
    marker = reader.u8()
    mode = marker >> 6
    if mode == 0:
        packed_values = [(marker << 24) | int.from_bytes(reader.take(3), "big")]
    else:
        if mode == 1:
            count = marker & 63
        elif mode == 2:
            count = ((marker & 63) << 8) | reader.u8()
        else:
            count = ((marker & 63) << 24) | int.from_bytes(reader.take(3), "big")
        if count > reader.remaining // 4:
            raise ReplayError("指令选择引用数量超过剩余字节边界。")
        packed_values = [reader.u32() for _ in range(count)]
    refs = []
    for packed in packed_values:
        kind = packed >> 28
        if kind not in REFERENCE_ENCODING_SOURCE["base_by_kind"]:
            raise ReplayError("选择引用类型超出原编码的四类范围。")
        offset = packed & 0x0FFFFFFF
        refs.append({"kind": kind, "original_id": offset + REFERENCE_ENCODING_SOURCE["base_by_kind"][kind],
                     "packed_raw": packed, "encoding_build": 11308})
    if references is not None:
        references.extend(refs)
    return [packed & 0x0FFFFFFF for packed in packed_values]


def _attribute(reader):
    """解析已确认的指令属性；未知属性不会尝试猜测长度。"""
    kind = reader.u8()
    if kind in (1, 15, 21, 30):
        return {"attribute_size": reader.u8()}
    if kind == 2:
        position = list(reader.unpack("fff"))
        if not all(math.isfinite(value) for value in position):
            raise ReplayError("指令目标坐标无效。")
        return {"target": {"x": position[0], "y": position[1], "z": position[2]}}
    if kind == 3:
        # 完整4原字节：两局1704次均为十亿层UID，低24位会丢掉3B并失去全部STLS交集。
        # 原选择引用读写已证此层与kind1有独立基数；不把第四字节再当padding（填充）。
        return {"target_entity_id": reader.u32(), "target_attribute_type_id": 3}
    if kind == 4:
        return {"target_entity_id": reader.u32(), "target_attribute_type_id": 4}
    if kind == 16:
        # 单个建筑采用大端 24 位编号，与单个单位的编码方向一致。
        reader.pos -= 1
        references = []
        units = _selected_units(reader, references)
        return {"producer_entity_id": units[0], "producer_ref": references[0], "units": units, "selected_refs": references}
    if kind == 0:
        reader.skip(2)
        return {}
    if kind == 32 or kind >= 64:
        reader.pos -= 1
        references = []
        return {"units": _selected_units(reader, references), "selected_refs": references}
    raise ReplayError("尚未识别的指令属性。")


def _decode_payload(command, reader):
    kind = command["type_id"]
    if kind in (146, 148, 152, 153):
        # 这些是控制包装或周期记录，不是可以评价的玩家操作。
        return
    if kind == 123:
        _building_placement(command, reader)
        return
    if kind in (3, 16):
        command.update(_attribute(reader))
        length = _attribute(reader).get("attribute_size")
        if length not in (10, 11) or length > reader.remaining:
            raise ReplayError("生产指令的参数长度结构不受支持。")
        command["queue_count_raw"] = reader.u8()
        blueprint = reader.unpack("i")[0]
        if blueprint <= 0:
            raise ReplayError("生产或科技蓝图编号无效。")
        command["unit_type_id" if kind == 3 else "research_type_id"] = blueprint
        command["queue_player_id"] = reader.u32()
        if command["queue_player_id"] != command["player_id"]:
            raise ReplayError("生产或科技参数的玩家编号与指令头矛盾。")
        tail = reader.take(length - 9)
        if tail not in (b"\x00", b"\x00\x00", b"\x00\x01"):
            raise ReplayError("生产或科技尾字段超出已核实范围。")
        command["queue_trailing_flags_raw"] = tail.hex()
    elif kind == 12:
        command.update(_attribute(reader))
        _target_attribute(command, reader)
    elif kind in (5, 14, 20, 22, 56):
        command.update(_attribute(reader))
        if kind in (20, 56):
            if reader.take(1) != b"\xff" or reader.remaining:
                raise ReplayError("实体卸载/取消建造的参数超出已核实范围。")
        elif kind == 22:
            _target_attribute(command, reader)
        else:
            _argument_frame(command, reader, allowed=(5,) if kind == 5 else (36, 37))
    elif kind in (62, 63, 65, 66, 67, 71, 72, 73, 76, 105, 109, 116):
        references = []
        command["units"] = _selected_units(reader, references)
        command["selected_refs"] = references
        if kind in (62, 65, 66, 67, 71, 73, 76, 105, 116):
            _target_attribute(command, reader)
        elif kind == 63:
            if reader.remaining and reader.take(1) != b"\xff":
                raise ReplayError("停止指令尾字段尚未识别。")
        elif kind == 109:
            if reader.remaining:
                _argument_frame(command, reader, allowed=(5,))
        elif kind == 72 and reader.remaining:
            if reader.data[reader.pos] in (1, 3, 6, 15, 21, 30):
                _target_attribute(command, reader)
            else:
                _argument_frame(command, reader, allowed=(36, 37, 42))


def _argument_frame(command, reader, allowed):
    """遍历已实证有界参数；保留未知子字段，不把原枚举动作等同于执行结果。"""
    tag, length = reader.u8(), reader.u8()
    if tag not in allowed or length != reader.remaining:
        raise ReplayError("指令参数标签或长度超出已核实结构。")
    body = reader.take(length)
    if tag == 5 and length != 4:
        raise ReplayError("四字节参数长度无效。")
    if tag == 36 and length != 6:
        raise ReplayError("能力参数36长度无效。")
    if tag == 37 and length not in (15, 23):
        raise ReplayError("能力参数37长度无效。")
    command["argument_frame_type_id"] = tag
    command["argument_frame_raw"] = body.hex()


def _building_placement(command, reader):
    """读取实证布局的建筑放置指令；不把下令或地基当作建造完成。

    同局 STLS（统计摘要）的建筑出现点与前两点交叉核验；墙段前两点
    不同，第三点用途仍未知。原始选择编号独立保存，不与紧凑编号混用。
    """
    player_slot = int.from_bytes(reader.take(4), "big")
    if reader.u8() != 27:
        raise ReplayError("建筑放置指令的属性结构尚未识别。")
    length = reader.u8()
    if length == 128:
        length = reader.u8()
        if length < 128:
            raise ReplayError("建筑放置指令的扩展长度无效。")
    elif length > 128:
        raise ReplayError("建筑放置指令的扩展长度尚未识别。")
    if length != reader.remaining or length < 55 or reader.u8() != 1:
        raise ReplayError("建筑放置指令的参数长度与实证布局不符。")
    building_type_id = reader.unpack("i")[0]
    points = [reader.unpack("fff") for _ in range(3)]
    if building_type_id <= 0 or not all(math.isfinite(value) for point in points for value in point):
        raise ReplayError("建筑放置指令的类型或坐标无效。")
    unknown_value = reader.u32()
    flags = reader.take(5).hex()
    count = reader.u8()
    if count == 0 or reader.remaining != count * 4:
        raise ReplayError("建筑放置指令的选择编号长度无效。")
    selected = [reader.u32() for _ in range(count)]
    command.update({
        "building_type_id": building_type_id,
        "target": dict(zip(("x", "y", "z"), points[0])),
        "placement_points": [dict(zip(("x", "y", "z"), point)) for point in points],
        "placement_selected_entity_ids_raw": selected,
        "placement_player_slot_raw": player_slot,
        "placement_unknown_value_raw": unknown_value,
        "placement_flags_raw": flags,
        "placement_outcome": "unknown",
    })


def _target_attribute(command, reader):
    # 目标外层标签与内部属性属于不同上下文。真实两局新增6=前缀+目标、3=位置+尾字段；
    # 不能把外层3误作内部实体属性3，也不把尚未确认用途的前缀/四float改称方向或路线。
    frame = reader.u8()
    if frame not in (1, 3, 6, 15, 21, 30):
        raise ReplayError("指令目标缺少已确认的有界外层字段。")
    length = reader.u8()
    command["target_frame_type_id"] = frame
    if length:
        if length > reader.remaining:
            raise ReplayError("指令目标长度超出边界。")
        target = _Reader(reader.data, reader.pos, reader.pos + length)
        decoded = {}
        if frame == 6:
            if length not in (9, 17):
                raise ReplayError("目标外层6的长度不在已实证结构内。")
            prefix = target.u32()
            expected = 1 if command.get("type_id") == 12 else 0 if command.get("type_id") == 62 else None
            if prefix not in (0, 1) or (expected is not None and prefix != expected):
                raise ReplayError("目标外层6的原前缀超出已实证范围。")
            decoded["target_frame_prefix_raw"] = prefix
        extended_position = frame == 3 or (frame == 30 and length == 42)
        if extended_position:
            if length not in (30, 42) or target.data[target.pos] != 2:
                raise ReplayError("目标外层3必须含已确认的位置及尾字段。")
        decoded.update(_attribute(target))
        if extended_position:
            if target.u8() != 5:
                raise ReplayError("目标外层3的尾字段标签尚未识别。")
            values = list(target.unpack("ffff"))
            if not all(math.isfinite(value) for value in values):
                raise ReplayError("目标尾字段含非有限数值。")
            decoded["target_extension_values_raw"] = values
            if frame == 30:
                tail = target.take(12)
                if tail != bytes.fromhex("000000000001000000000000"):
                    raise ReplayError("目标外层30的尾字段超出本次已核实样本。")
                decoded["target_frame_tail_raw"] = tail.hex()
        if (frame in (3, 6) or extended_position) and target.remaining:
            raise ReplayError("新目标外层仍有未识别的尾字段。")
        command.update(decoded)
        # 目标内部未解出的字节也计入剩余字段，不能宣称完整解码。
        command["target_bytes_remaining"] = target.remaining
        reader.skip(length)


def _command(reader, tick, host_id, counters):
    start = reader.pos
    size, kind, flags, serial = reader.unpack("hBBH")
    if size < 24 or start + size > reader.end:
        raise ReplayError(f"录像在位置 {start} 处的指令长度无效。")
    reader.skip(14)
    player_id = reader.u32()
    name = _COMMAND_NAMES.get(kind, "unknown")
    command = {
        "time": tick / _TICKS_PER_SECOND, "tick": tick, "player_id": player_id,
        "type": name, "type_id": kind, "units": [], "selected_refs": [], "queued": None,
        "queued_candidate": bool(flags & 128),
        "command_flags_raw": flags,  # 原始头标志；高位排队仅是候选语义，不能当实际执行状态。
        "offset": start, "size": size, "known": name != "unknown",
        "category": "control" if name == "control" else "periodic" if name == "periodic"
        else "system" if player_id == 0 else "action",
        "host_id": host_id, "sequence": serial,
        "is_system": player_id == 0 or name in ("control", "periodic"),
    }
    payload = _Reader(reader.data, reader.pos, start + size)
    if command["known"]:
        try:
            candidate = dict(command)
            _decode_payload(candidate, payload)
            command.update(candidate)
        except ReplayError:
            # 外层指令长度仍然可靠，因此保留此记录、跳过负载；不丢失后续记录。
            command["known"] = False
            command["payload_supported"] = False
            counters["payload_errors"] += 1
    command["payload_bytes_remaining"] = payload.remaining
    command["payload_decoded"] = bool(command["known"] and payload.remaining == 0
                                      and not command.get("target_bytes_remaining")
                                      and name not in ("control", "periodic"))
    command.setdefault("payload_structure_decoded", command["payload_decoded"])
    reader.pos = start + size
    return command


def _stream(reader, commands, counters):
    last_tick = 0
    tick_count = 0
    while reader.remaining:
        start = reader.pos
        record_type = reader.u32()
        size = reader.u32()
        end = reader.pos + size
        if size > reader.remaining:
            raise ReplayError(f"录像在位置 {start} 处截断；没有返回不完整的分析。")
        record = _Reader(reader.data, reader.pos, end)
        if record_type == 0:
            if size < 13:
                raise ReplayError("录像时间记录长度无效。")
            marker = record.u8()
            tick, _state_hash, blocks = record.unpack("III")
            if marker != 32 or tick < last_tick or blocks > record.remaining // 12:
                raise ReplayError(f"录像在位置 {start} 处的时间记录结构不受支持。")
            last_tick = tick
            tick_count += 1
            for _ in range(blocks):
                host, _reserved, length = record.unpack("III")
                if length > record.remaining:
                    raise ReplayError("录像指令块超出时间记录边界。")
                block = _Reader(record.data, record.pos, record.pos + length)
                while block.remaining:
                    commands.append(_command(block, tick, host, counters))
                record.skip(length)
            if record.remaining:
                raise ReplayError("录像时间记录包含未识别的尾部数据。")
        elif record_type == 1:
            # 聊天记录按长度越过，不解码、不导出聊天文字。
            if size < 8:
                raise ReplayError("录像聊天记录长度无效。")
            counters["chat_records_skipped"] += 1
        else:
            raise ReplayError(f"暂不支持录像记录类型 {record_type}，未继续猜测解析。")
        reader.pos = end
    if not tick_count:
        raise ReplayError("文件没有完整录像时间线；可能是对局总结或不支持的格式。")
    return last_tick, tick_count


def _read_zip_replay(raw):
    """从 ZIP（压缩包）内只读唯一回放，不将任何成员写到磁盘。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            candidates = []
            members = archive.infolist()
            if len(members) > 1000:
                raise ReplayError("压缩包内文件过多；请先解压并选择其中一场录像。")
            for member in members:
                if member.is_dir() or member.file_size < 76:
                    continue
                # 官网文件可能名为 AgeIV_Replay_比赛编号、没有扩展名，故只看文件头。
                with archive.open(member) as stream:
                    prefix = stream.read(76)
                if len(prefix) == 76 and prefix[4:12] == b"AOE4_RE\x00":
                    candidates.append(member)
                    if len(candidates) > 1:
                        raise ReplayError("压缩包内有多场录像；请先解压，再选择其中一场导入。")
            if not candidates:
                raise ReplayError("压缩包内没有可识别的帝国时代 4 完整录像。")
            member = candidates[0]
            if member.file_size > _MAX_EXPANDED_BYTES:
                raise ReplayError("录像解压后过大；当前支持不超过 512 MiB 的内容。")
            with archive.open(member) as stream:
                data = stream.read(_MAX_EXPANDED_BYTES + 1)
            if len(data) > _MAX_EXPANDED_BYTES:
                raise ReplayError("录像解压后过大；当前支持不超过 512 MiB 的内容。")
            return data, member.filename
    except ReplayError:
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile,
            zipfile.LargeZipFile, zlib.error) as exc:
        raise ReplayError("录像压缩包损坏、尚未下载完整，或采用了不支持的加密及压缩方式。") from exc


def parse_replay(path):
    """将用户选中的完整录像、.gz 或官网 ZIP 文件解析为版本 1 的字典。

    offset（偏移）对应解压后的原始文件；time（秒）使用格式记录的每秒
    8 个逻辑刻。commands（指令）不代表物理按键，也不代表执行成功。
    """
    selected = Path(path)
    try:
        if not selected.is_file():
            raise ReplayError("请选择一个已经保存的录像文件。")
        size = selected.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ReplayError("录像文件过大；当前支持不超过 256 MiB 的本地文件。")
        raw = selected.read_bytes()
    except ReplayError:
        raise
    except (OSError, ValueError) as exc:
        raise ReplayError("无法读取选中的录像文件。") from exc
    if len(raw) > _MAX_FILE_BYTES:
        raise ReplayError("录像文件过大；当前支持不超过 256 MiB 的本地文件。")
    packed = raw.startswith(b"\x1f\x8b")
    archive_member = None
    source_format = "aoe4-replay"
    if raw.startswith(b"PK"):
        data, archive_member = _read_zip_replay(raw)
        source_format = "aoe4-replay-zip"
    elif packed:
        try:
            # 有界解压，防止损坏或异常压缩文件消耗全部内存。
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data = stream.read(_MAX_EXPANDED_BYTES + 1)
        except (OSError, EOFError, zlib.error) as exc:
            raise ReplayError("压缩录像损坏或尚未完整保存。") from exc
        source_format = "aoe4-replay-gzip"
    else:
        data = raw
    if len(data) > _MAX_EXPANDED_BYTES:
        raise ReplayError("录像解压后过大；当前支持不超过 512 MiB 的内容。")
    if len(data) < 76 or data[4:12] != b"AOE4_RE\x00":
        raise ReplayError("这不是受支持的帝国时代 4 完整录像；请选择录像文件、官网 ZIP 或完整录像 .gz 文件。")
    reader = _Reader(data, 76)
    chunks = []
    _chunk_file(reader, chunks)
    _chunk_file(reader, chunks)
    _read_chunk(reader, chunks)
    warnings = []
    players = None
    setup = next((chunk for chunk in chunks if chunk[:2] == (b"DATA", b"DATA") and chunk[2] >= 50), None)
    if setup:
        try:
            players = _players_from_setup(data, setup)
        except ReplayError:
            warnings.append("玩家资料结构尚未完整识别，按录像指令中的编号显示玩家。")
        if players is None and not warnings:
            warnings.append(f"玩家资料版本 {setup[2]} 尚未支持，按录像指令中的编号显示玩家。")
    else:
        warnings.append("录像没有可识别的玩家资料，按指令中的编号显示玩家。")
    commands = []
    counters = Counter()
    last_tick, tick_count = _stream(reader, commands, counters)
    if players is None:
        players = []
    ids = {player["id"] for player in players}
    for player_id in sorted({command["player_id"] for command in commands} - ids - {0}):
        players.append({"id": player_id, "profile_id": None, "name": f"玩家 {player_id}",
                        "civilization": None, "team": None})
    unknown = Counter(str(command["type_id"]) for command in commands if not command["known"])
    if unknown:
        warnings.append("部分指令类型或属性尚未识别；已保留原始编号，未将其猜作有效操作。")
    warnings.append("记录的是游戏指令，并非完整键鼠输入；指令发出不代表单位已生产或操作成功。")
    warnings.append("录像时长由最后记录的逻辑刻换算，不代表比赛已经正常结束；排队标记尚未做实机交叉验证。")
    warnings.append("此文件尚未提取资源、完整单位轨迹、视野或伤害，不能据此断言断村、卡人口或战斗原因。")
    build = int.from_bytes(data[2:4], "little")
    # 引用编码的基数与原读写证明目前仅绑定11308；旧构建保留packed/kind，不冒称已知原UID。
    for command in commands:
        command["selected_refs_verified"] = build == REFERENCE_ENCODING_SOURCE["build_number"]
        if not command["selected_refs_verified"]:
            for reference in command.get("selected_refs", []):
                reference["original_id"] = None
            if isinstance(command.get("producer_ref"), dict):
                command["producer_ref"]["original_id"] = None
    source = {"name": selected.name, "sha256": hashlib.sha256(raw).hexdigest(),
              "format": source_format, "size": len(raw)}
    if archive_member is not None:
        source["archive_member"] = archive_member
    return {
        "schema_version": 1,
        "source": source,
        "metadata": {"game_version": None, "build_number": build or None, "map": None,
                     "duration": last_tick / _TICKS_PER_SECOND,
                     "duration_source": "last_recorded_tick", "players": players},
        "commands": commands, "events": [], "resources": [],
        "coverage": {"commands_total": len(commands),
                     "commands_identified": sum(command["known"] for command in commands),
                     "commands_decoded": sum(command["payload_decoded"] for command in commands),
                     "commands_structurally_decoded": sum(command["payload_structure_decoded"] for command in commands),
                     "unknown_structurally_decoded": sum(not command["known"] and command["payload_structure_decoded"] for command in commands),
                     "command_enum_source": COMMAND_ENUM_SOURCE,
                     "reference_encoding_source": REFERENCE_ENCODING_SOURCE if build == REFERENCE_ENCODING_SOURCE["build_number"] else None,
                     "decoded_definition": "指令参数已全部遍历；不包含控制和周期记录。",
                     "unknown_types": dict(unknown), "warnings": warnings,
                     "ticks_per_second": _TICKS_PER_SECOND, "ticks_total": tick_count,
                     "stream_complete": True, "metadata_version": setup[2] if setup else None,
                     "chat_records_skipped": counters["chat_records_skipped"],
                     "payload_errors": counters["payload_errors"],
                     "expanded_size": len(data)},
    }
