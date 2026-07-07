# -*- coding: utf-8 -*-
"""
common_matrix_utils_local_v13.py

本地版矩阵信号差异识别公共工具函数。
只读取本地 input\\4.0 和 input\\5.1，不包含任何外部下载逻辑。

本版口径：
1. ECU 标准化状态使用小写：
   R/r -> r，表示 receive / 接收
   T/t/S/s -> s，表示 send / 发送
2. 支持解析 4.0 括号格式：
   TCU_PHEV:R(HighRegulationArea:R,LowRegulationArea:R)
3. 信号值描述增强：
   支持枚举范围写法，例如 0x1~0x6: Reserved、0x1-0x6: Reserved、0x1`0x6: Reserved。
   会展开为 0x1、0x2、0x3、0x4、0x5、0x6 后再比较。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


EXCEL_EXTS = (".xlsx", ".xlsm")


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u3000", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def norm_header(value: Any) -> str:
    text = cell_text(value).lower()
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)


def is_excel_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in EXCEL_EXTS
        and not path.name.startswith("~$")
    )


def normalize_ecu_state(value: Any) -> str:
    """
    ECU 收发状态标准化，小写输出：
    R/r -> r，接收
    T/t/S/s -> s，发送
    X/x/× -> x，不参与
    """
    v = cell_text(value)
    if not v:
        return ""
    u = v.upper()
    if u == "R":
        return "r"
    if u in {"T", "S"}:
        return "s"
    if u in {"X", "×"}:
        return "x"
    return ""


def is_status_value(value: Any) -> bool:
    return normalize_ecu_state(value) in {"r", "s", "x"}


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        item = cell_text(item)
        if not item:
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def join_lines(items: Iterable[str]) -> str:
    return "\n".join(unique_keep_order(items))


def join_unique_status_texts(texts: Iterable[str]) -> str:
    """
    去重阶段使用：保持原有 ECU 状态文本格式，按行合并去重。
    这样 4.0 的括号格式不会被拆散：
    TCU_PHEV:r(HighRegulationArea:r,LowRegulationArea:r)
    """
    lines: List[str] = []
    for text in texts:
        s = cell_text(text)
        if not s:
            continue
        for line in s.split("\n"):
            line = cell_text(line)
            if line:
                lines.append(line)
    return "\n".join(unique_keep_order(lines))


def parse_status_lines(text: Any) -> List[Tuple[str, str]]:
    """
    解析 ECU 状态文本。

    支持普通格式：
    EMS_PHEV:r
    HCU_PT:s

    也支持 4.0 括号格式：
    TCU_PHEV:r(HighRegulationArea:r,LowRegulationArea:r)
    """
    s = cell_text(text)
    if not s:
        return []

    # 把括号内容拆成同级片段，便于统一解析
    # A:r(B:r,C:r) -> A:r, B:r, C:r
    s = s.replace("(", ",").replace(")", "")
    result: List[Tuple[str, str]] = []

    for part in re.split(r"[\n;；,，]+", s):
        part = cell_text(part)
        if not part:
            continue
        if ":" in part:
            ecu, state = part.split(":", 1)
        elif "=" in part:
            ecu, state = part.split("=", 1)
        else:
            continue

        ecu = cell_text(ecu)
        state = cell_text(state)

        # 如果 state 后面还有多余内容，只取第一个状态字符
        if state:
            state = state[0]

        if ecu and state:
            result.append((ecu, state))

    return result


def aggregate_status_texts(texts: Iterable[str], normalize: bool = False) -> str:
    """
    聚合多条 ECU 状态文本。
    同一个 ECU 若出现多个状态，会输出 ECU:r/s。
    """
    order: List[str] = []
    state_map: Dict[str, List[str]] = {}

    for text in texts:
        for ecu, state in parse_status_lines(text):
            use_state = normalize_ecu_state(state) if normalize else cell_text(state)
            if not use_state or use_state == "x":
                continue
            if ecu not in state_map:
                state_map[ecu] = []
                order.append(ecu)
            if use_state not in state_map[ecu]:
                state_map[ecu].append(use_state)

    return "\n".join(f"{ecu}:{'/'.join(state_map[ecu])}" for ecu in order)


def split_send_receive_from_standard_status(status_text: Any) -> Tuple[str, str]:
    """
    从标准化状态中拆分发送 ECU 和接收 ECU。
    标准化状态：r=接收，s=发送。
    """
    send: List[str] = []
    recv: List[str] = []
    for ecu, state in parse_status_lines(status_text):
        states = [x.strip().lower() for x in re.split(r"[/,，]+", state) if x.strip()]
        if "s" in states:
            send.append(ecu)
        if "r" in states:
            recv.append(ecu)
    return join_lines(send), join_lines(recv)


def try_decimal(value: Any) -> Optional[Decimal]:
    s = cell_text(value)
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def normalize_hex_codes(text: str) -> str:
    def repl(match):
        raw = match.group(0)
        try:
            return hex(int(raw, 16))
        except Exception:
            return raw.lower()
    return re.sub(r"0x[0-9a-fA-F]+", repl, text)


def _parse_enum_code_to_int(code: str) -> Optional[int]:
    code = cell_text(code).lower()
    if not code:
        return None
    try:
        if code.startswith("0x"):
            return int(code, 16)
        return int(code)
    except Exception:
        return None


def _format_enum_code(n: int) -> str:
    return hex(n)


def _normalize_enum_desc(desc: str) -> str:
    """
    枚举描述归一化：
    - 去空格
    - 去结尾分号、逗号
    - 小写
    """
    desc = cell_text(desc).lower()
    desc = normalize_hex_codes(desc)
    desc = re.sub(r"\s+", "", desc)
    desc = re.sub(r"[;；,，]+$", "", desc)
    return desc


def _expand_enum_key(key: str) -> List[str]:
    """
    支持：
    0x1
    1
    0x1~0x6
    0x1-0x6
    0x1`0x6
    1~6
    """
    key = cell_text(key).lower()
    key = key.replace("～", "~").replace("–", "-").replace("—", "-")
    key = key.replace("｀", "`")

    # 部分 Excel / 字体下，范围符号可能被复制成反引号
    # 统一按范围连接符处理。
    range_separators = ["~", "`", "-"]

    sep_found = ""
    for sep in range_separators:
        if sep in key:
            sep_found = sep
            break

    if not sep_found:
        n = _parse_enum_code_to_int(key)
        return [_format_enum_code(n)] if n is not None else []

    start_raw, end_raw = key.split(sep_found, 1)
    start = _parse_enum_code_to_int(start_raw)
    end = _parse_enum_code_to_int(end_raw)
    if start is None or end is None:
        return []

    if start <= end:
        nums = range(start, end + 1)
    else:
        nums = range(end, start + 1)

    return [_format_enum_code(n) for n in nums]


def _parse_enum_description(value: Any) -> Optional[Dict[str, str]]:
    """
    将信号值描述解析成 枚举值 -> 描述 的字典。

    能处理：
    0x0: SNA
    0x1~0x6: Reserved
    0x1-0x6: Reserved
    0x1`0x6: Reserved
    0xF: POS_ZERO
    """
    s = cell_text(value).lower()
    if not s:
        return {}

    s = normalize_hex_codes(s)
    s = s.replace("：", ":")
    s = s.replace("；", ";")
    s = s.replace("～", "~")
    s = s.replace("｀", "`")
    s = re.sub(r"\s+", " ", s).strip()

    # 枚举 key：单值或范围
    # 范围连接符支持 ~、-、`、–、—
    single_code = r"(?:0x[0-9a-f]+|\d+)"
    enum_key = rf"{single_code}\s*(?:[~`\-–—]\s*{single_code})?"

    pattern = re.compile(
        rf"({enum_key})\s*:\s*(.*?)(?=(?:{enum_key})\s*:|$)",
        re.I | re.S,
    )

    matches = pattern.findall(s)
    if not matches:
        return None

    result: Dict[str, str] = {}

    for key, desc in matches:
        codes = _expand_enum_key(key)
        desc_norm = _normalize_enum_desc(desc)
        if not codes:
            continue
        for code in codes:
            result[code] = desc_norm

    return result


def normalize_description(value: Any) -> str:
    """
    信号值描述比较用归一化。

    已支持：
    1. 大小写差异
    2. 空格 / 换行差异
    3. 中文/英文冒号、分号差异
    4. 0x01 与 0x1
    5. 枚举顺序差异
    6. 枚举范围合并写法：
       0x1~0x6: Reserved
       0x1-0x6: Reserved
       0x1`0x6: Reserved
    """
    enum_dict = _parse_enum_description(value)

    if enum_dict is not None:
        items = []
        for code in sorted(enum_dict.keys(), key=lambda x: int(x, 16) if x.startswith("0x") else int(x)):
            items.append(f"{code}:{enum_dict[code]}")
        return "|".join(items)

    # 如果不是枚举格式，按普通文本归一化
    s = cell_text(value).lower()
    if not s:
        return ""
    s = normalize_hex_codes(s)
    s = s.replace("；", ";").replace("：", ":")
    return re.sub(r"\s+", "", s)


def values_equal(field_code: str, v1: Any, v2: Any) -> bool:
    numeric_fields = {"bit_length", "resolution", "offset", "signal_min", "signal_max"}
    if field_code in numeric_fields:
        d1 = try_decimal(v1)
        d2 = try_decimal(v2)
        if d1 is not None and d2 is not None:
            return d1 == d2

    if field_code == "signal_value_description":
        return normalize_description(v1) == normalize_description(v2)

    s1 = re.sub(r"\s+", "", cell_text(v1)).lower()
    s2 = re.sub(r"\s+", "", cell_text(v2)).lower()
    return s1 == s2


def strip_vcu_hcu_prefix(name: Any) -> str:
    s = cell_text(name)
    return re.sub(r"^(VCU|HCU)[_\-]?", "", s, flags=re.IGNORECASE)
