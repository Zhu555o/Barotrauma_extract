#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Barotrauma_locale_extract.py — Barotrauma 模组本地化提取工具

对任意 Barotrauma 模组（重点是未本地化的创意工坊模组）自动提取全部可译文本，
按游戏官方 infotexts 格式生成目标语言（默认简体中文）本地化文件，值填英文原文，
供译者直接覆写翻译。支持两种输出模式：

  separate（默认）：生成独立「<模组名> 汉化包」模组（filelist.xml + 本地化文件），
                    玩家在原模组之后启用即可生效；不改原模组、不怕工坊更新、联机安全。
  inplace：        在原模组内新建 Localization/ 目录并注册进其 filelist.xml，
                    自动按游戏算法重算 expectedhash（写入前备份 filelist.xml.bak）。

提取规则均对照游戏源码（FakeFishGames/Barotrauma）逐条核实：
  - 物品/结构:     entityname/entitydescription.<nameidentifier|identifier>（键优先于内联 name）
  - 角色:          character.<speciesname|speciestranslationoverride>
  - 疾病:          afflictionname/description/causeofdeath/causeofdeathself.<translationoverride|identifier>
  - 职业:          jobname/jobdescription.<identifier>
  - 任务:          missionname/missiondescription.<textidentifier|identifier>
  - 升级模块/分类: upgradename/upgradedescription/upgradecategory.<...>（内联名优先，见报告）
  - 天赋:          talentname/talentdescription.<identifier>（或 nameidentifier 原始键）
  - 阵营:          faction.<id>[.description/.shortdescription]（内联优先，见报告）
  - 物品标签:      tagname/tagdescription.<...>
  - 事件:          eventname.<id>；已标签化文本转出；单词原文以其本身为键；
                   多词原文无法经汉化包覆盖（可 --rewrite-events 就地标签化）
  - 模组自带文本文件: 全部键整包转出
  - NPC 对话文件:  生成目标语言副本（line 保留原文待译）

用法示例：
  python Barotrauma_locale_extract.py "C:/.../WorkshopMods/Installed/2282010683"
  python Barotrauma_locale_extract.py "C:/.../WorkshopMods/Installed"            # 批量
  python Barotrauma_locale_extract.py <模组路径> --mode inplace --rewrite-events
  python Barotrauma_locale_extract.py <目录> --verify-hash                       # 仅校验哈希算法

仅依赖 Python 标准库（3.8+）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERSION = "1.0.0"

# ============================================================================
# 常量表（对照游戏源码）
# ============================================================================

# 这些 filelist 类型的文件哈希 = 16 个零字节（HashlessFile/OtherFile 系）
HASHLESS_TYPES = {
    "other", "none", "uistyle", "sounds", "particles",
    "serverexecutable", "backgroundcreatureprefabs",
}
# 这些类型按原始字节 MD5（BaseSubFile 系：.sub 压缩文件）
BYTES_PERFECT_TYPES = {"submarine", "wreck", "enemysubmarine", "beaconstation"}
# 其余全部按「UTF-8 文本去空白后 MD5」

# 语言表：小写语言名 → (规范写法, nowhitespace, translatedname)
LANGUAGES: Dict[str, Tuple[str, str, str]] = {
    "english":              ("English", "false", "English"),
    "simplified chinese":   ("Simplified Chinese", "true", "中文（简体）"),
    "traditional chinese":  ("Traditional Chinese", "true", "中文（繁體）"),
    "brazilian portuguese": ("Brazilian Portuguese", "false", "Português brasileiro"),
    "castilian spanish":    ("Castilian Spanish", "false", "Castellano"),
    "latinamerican spanish": ("Latinamerican Spanish", "false", "Español Latinoamericano"),
    "french":               ("French", "false", "Français"),
    "german":               ("German", "false", "Deutsch"),
    "japanese":             ("Japanese", "true", "日本語"),
    "korean":               ("Korean", "true", "한국어"),
    "polish":               ("Polish", "false", "Polski"),
    "russian":              ("Russian", "false", "Русский"),
    "turkish":              ("Turkish", "false", "Türkçe"),
    "finnish":              ("Finnish", "false", "Suomi"),
    "czech":                ("Czech", "false", "Čeština"),
    "danish":               ("Danish", "false", "Dansk"),
    "dutch":                ("Dutch", "false", "Nederlands"),
    "italian":              ("Italian", "false", "Italiano"),
    "norwegian":            ("Norwegian", "false", "Norska"),
    "european portuguese":  ("European Portuguese", "false", "Português europeu"),
    "spanish":              ("Spanish", "false", "Español"),
    "swedish":              ("Swedish", "false", "Svenska"),
    "ukrainian":            ("Ukrainian", "false", "Украї́нська"),
    "chinese":              ("Simplified Chinese", "true", "中文（简体）"),  # 新版语言选项别名
}
# 中文别名
LANG_ALIASES: Dict[str, str] = {
    "简体中文": "simplified chinese", "简体": "simplified chinese",
    "繁体中文": "traditional chinese", "繁体": "traditional chinese",
    "schinese": "simplified chinese", "tchinese": "traditional chinese",
    "sc": "simplified chinese", "tc": "traditional chinese",
}
DEFAULT_TARGET_LANG = "Simplified Chinese"

# 原版语言 → Content/Texts 下文件夹名
VANILLA_TEXT_DIRS = {
    "english": "English",
    "simplified chinese": "SimplifiedChinese",
    "traditional chinese": "TraditionalChinese",
    "brazilian portuguese": "BrazilianPortuguese",
    "castilian spanish": "CastilianSpanish",
    "latinamerican spanish": "LatinamericanSpanish",
    "french": "French",
    "german": "German",
    "japanese": "Japanese",
    "korean": "Korean",
    "polish": "Polish",
    "russian": "Russian",
    "turkish": "Turkish",
    "finnish": "FinnishVanilla",  # 原版只有 FinnishVanilla.xml
}

WINDOWS_INVALID_FN = set('<>:"/\\|?*')

# XML 元素名合法性（用于把单词原文当键）
_KEY_NAME_RE = re.compile(r"^[^\d\W][\w.\-]*$", re.UNICODE)


# ============================================================================
# 文本 / XML 工具
# ============================================================================

def read_text_robust(path: Path) -> str:
    """模拟 .NET File.ReadAllText(path, Encoding.UTF8)：识别 BOM，坏字节替换为 U+FFFD。"""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"\x00\x00\xfe\xff"):
        return data.decode("utf-32", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def parse_xml_text(text: str) -> Optional[ET.Element]:
    """容错解析；声明带编码时先剥离。"""
    text = _XML_DECL_RE.sub("", text, count=1)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def load_xml_file(path: Path) -> Optional[ET.Element]:
    try:
        return parse_xml_text(read_text_robust(path))
    except OSError:
        return None


def tag_lower(elem: ET.Element) -> str:
    t = elem.tag
    return t.lower() if isinstance(t, str) else ""


def ci_attrs(elem: ET.Element) -> Dict[str, str]:
    return {k.lower(): (v if v is not None else "") for k, v in elem.attrib.items()}


def ci_get(elem: ET.Element, name: str, default: str = "") -> str:
    v = elem.attrib.get(name)
    if v is not None:
        return v
    low = name.lower()
    for k, val in elem.attrib.items():
        if k.lower() == low:
            return val if val is not None else ""
    return default


def norm_value(raw: str) -> str:
    """文本值规范化：去首尾空白，真实换行转为字面 \\n（游戏会转回）。"""
    v = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    return v


def valid_key(key: str) -> bool:
    return bool(_KEY_NAME_RE.match(key)) and len(key) <= 200


def xml_escape_text(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def sanitize_filename(name: str) -> str:
    out = "".join(("_" if c in WINDOWS_INVALID_FN else c) for c in name).strip().strip(".")
    reserved = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5",
                "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4",
                "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"}
    if out.lower() in reserved:
        out = "_" + out
    return out or "unnamed"


# ============================================================================
# expectedhash 算法复现（ContentPackage.cs / Md5Hash.cs / ContentFile.cs）
# ============================================================================

_LATIN1_WS = set("\t\n\x0b\x0c\r \x85\xa0")


def dotnet_is_white(ch: str) -> bool:
    """等价 C# char.IsWhiteSpace（Latin1 表 + 非 Latin1 的 Zs 类别）。"""
    if ord(ch) < 0x100:
        return ch in _LATIN1_WS
    return unicodedata.category(ch) == "Zs"


def file_hash_text_md5(path: Path) -> bytes:
    text = read_text_robust(path)
    stripped = "".join(ch for ch in text if not dotnet_is_white(ch))
    return hashlib.md5(stripped.encode("utf-8", errors="replace")).digest()


def file_hash_bytes_md5(path: Path) -> bytes:
    h = hashlib.md5()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


BLANK_HASH = bytes(16)


def content_file_hash(ctype: str, path: Optional[Path]) -> Optional[bytes]:
    """单个注册文件的哈希；文件缺失返回 None（游戏同样会把它排除出 Files）。"""
    if ctype in HASHLESS_TYPES:
        return BLANK_HASH
    if path is None or not path.is_file():
        return None
    try:
        if ctype in BYTES_PERFECT_TYPES:
            return file_hash_bytes_md5(path)
        return file_hash_text_md5(path)
    except OSError:
        return None


def package_hash(entries: List[Tuple[str, Optional[Path]]], name: str, modversion: str) -> str:
    """包哈希：按注册顺序串联各文件 16 字节哈希 + 包名 + modversion，MD5 大写十六进制。"""
    h = hashlib.md5()
    for ctype, path in entries:
        fh = content_file_hash(ctype, path)
        if fh is None:
            continue
        h.update(fh)
    if name:
        h.update(name.encode("utf-8"))
    h.update(modversion.encode("utf-8"))
    return h.hexdigest().upper()


# ============================================================================
# 路径解析（%ModDir% / 大小写不敏感）
# ============================================================================

_MODDIR_RE = re.compile(r"%ModDir:([^%]*)%", re.IGNORECASE)


def resolve_content_path(raw: str, mod_root: Path,
                         cross_mod_lookup=None,
                         own_name: str = "",
                         own_workshopid: str = "") -> Optional[Path]:
    """解析 filelist 的 file 属性（对照 ContentPath.cs）：
    %ModDir% → 模组根（大小写不敏感）；
    %ModDir:本模组名% / %ModDir:本模组workshopid% → 同样是模组根；
    %ModDir:其他模组% → 其他模组目录（尽力解析，失败按缺失处理）。
    """
    mod_root_str = str(mod_root)

    def repl(m: "re.Match") -> str:
        name = m.group(1).strip()
        if not name:
            return m.group(0)
        low = name.lower()
        if own_name and low == own_name.strip().lower():
            return mod_root_str
        if own_workshopid and low == own_workshopid.strip().lower():
            return mod_root_str
        if cross_mod_lookup is not None:
            other = cross_mod_lookup(name)
            if other is not None:
                return str(other)
        return m.group(0)  # 无法解析则原样保留（后续按缺失处理）

    p = _MODDIR_RE.sub(repl, raw)
    p = re.sub(r"%ModDir%", lambda _m: mod_root_str, p, flags=re.IGNORECASE)
    p = p.replace("\\", "/")
    path = Path(p)
    if path.exists():
        return path
    # Windows 大小写不敏感回退
    return find_case_insensitive(path)


def find_case_insensitive(path: Path) -> Optional[Path]:
    if path.exists():
        return path
    parts = list(path.parts)
    if len(parts) < 2:
        return None
    cur = Path(parts[0])
    rest = parts[1:]
    if not cur.exists():
        return None
    for seg in rest:
        if seg in (".", ".."):
            cur = cur / seg
            continue
        found = None
        try:
            for child in cur.iterdir():
                if child.name.lower() == seg.lower():
                    found = child
                    break
        except OSError:
            return None
        if found is None:
            return None
        cur = found
    return cur if cur.exists() else None


# ============================================================================
# 游戏目录探测 & 原版键加载（避免覆盖原版已有翻译）
# ============================================================================

def detect_game_dir(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if (p / "Content" / "Texts").is_dir() else None
    candidates: List[Path] = []
    for root in (Path("C:/Program Files (x86)/Steam"),
                 Path("C:/Program Files/Steam")):
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                    candidates.append(Path(m.group(1)) / "steamapps" / "common" / "Barotrauma")
            except OSError:
                pass
    for c in candidates:
        if (c / "Content" / "Texts").is_dir():
            return c
    return None


def load_vanilla_pack(game_dir: Path, lang_key: str) -> Dict[str, str]:
    """读取原版某语言的全部文本键值（key 小写 → value）。"""
    texts: Dict[str, str] = {}
    d = game_dir / "Content" / "Texts"
    sub = VANILLA_TEXT_DIRS.get(lang_key)
    folders = [d / sub] if sub and (d / sub).is_dir() else []
    if lang_key == "english":
        folders.append(d / "English")
    seen = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        for f in sorted(folder.glob("*.xml")):
            root = load_xml_file(f)
            if root is None:
                continue
            for el in root.iter():
                t = tag_lower(el)
                if t and t != "infotexts" and t != "override" and el.text is not None:
                    texts.setdefault(t, el.text.strip())
    return texts


# ============================================================================
# filelist.xml 解析
# ============================================================================

class FileEntry:
    __slots__ = ("ctype", "raw_path", "path")

    def __init__(self, ctype: str, raw_path: str, path: Optional[Path]):
        self.ctype = ctype          # 小写类型名
        self.raw_path = raw_path    # 原始 file 属性
        self.path = path            # 解析后的绝对路径（可能 None）

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.is_file()


class FileList:
    def __init__(self, mod_root: Path):
        self.mod_root = mod_root
        self.raw_text = ""
        self.name = ""
        self.modversion = "1.0.0"
        self.gameversion = ""
        self.corepackage = "False"
        self.steamworkshopid = ""
        self.expectedhash = ""
        self.entries: List[FileEntry] = []
        self.warnings: List[str] = []
        self.error: Optional[str] = None

    @property
    def has_target_text(self) -> bool:
        return any(e.ctype == "text" for e in self.entries)


def parse_filelist(mod_root: Path, cross_mod_lookup=None) -> FileList:
    fl = FileList(mod_root)
    fl_path = mod_root / "filelist.xml"
    if not fl_path.is_file():
        # 大小写回退
        alt = find_case_insensitive(mod_root / "FileList.xml")
        if alt is not None:
            fl_path = alt
        else:
            fl.error = "缺少 filelist.xml"
            return fl
    fl.raw_text = read_text_robust(fl_path)
    root = parse_xml_text(fl.raw_text)
    if root is None or tag_lower(root) != "contentpackage":
        fl.error = "filelist.xml 解析失败或根元素不是 contentpackage"
        return fl
    fl.name = ci_get(root, "name", "").strip()
    fl.modversion = ci_get(root, "modversion", "1.0.0").strip() or "1.0.0"
    fl.gameversion = ci_get(root, "gameversion", "").strip()
    fl.corepackage = ci_get(root, "corepackage", "False")
    fl.steamworkshopid = ci_get(root, "steamworkshopid", "").strip()
    fl.expectedhash = ci_get(root, "expectedhash", "").strip()

    # 记录条目（行号定位不再需要：插入统一在 </contentpackage> 前进行）
    idx = 0
    for child in root:
        ctype = tag_lower(child)
        raw = ci_get(child, "file", "").strip()
        idx += 1
        if not raw:
            fl.warnings.append(f"filelist 第 {idx} 个条目 <{ctype}> 缺少 file 属性")
            continue
        resolved = resolve_content_path(raw, mod_root, cross_mod_lookup,
                                        own_name=fl.name, own_workshopid=fl.steamworkshopid)
        if resolved is None:
            fl.warnings.append(f"无法解析路径: {ctype} → {raw}")
        fl.entries.append(FileEntry(ctype, raw, resolved))
    if not fl.name:
        fl.error = "contentpackage 缺少 name 属性"
    return fl


def find_mod_dirs(input_path: Path) -> Tuple[List[Path], bool]:
    """返回 (模组目录列表, 是否批量)。"""
    if input_path.is_file() and input_path.name.lower() == "filelist.xml":
        return [input_path.parent], False
    if (input_path / "filelist.xml").is_file():
        return [input_path], False
    # 批量：子目录中含 filelist.xml 的
    mods = []
    if input_path.is_dir():
        for child in sorted(input_path.iterdir()):
            if child.is_dir() and (child / "filelist.xml").is_file():
                mods.append(child)
    return mods, bool(mods)


# ============================================================================
# 提取结果模型
# ============================================================================

class ExtractResult:
    def __init__(self):
        self.keys: Dict[str, str] = {}            # key(小写) → value
        self.key_group: Dict[str, str] = {}       # key → 组标签
        self.groups: List[Tuple[str, List[Tuple[str, str]]]] = []
        self.conflicts: List[Tuple[str, str, str]] = []
        self.no_source_name_keys: List[str] = []   # 无原文、用标识符占位的名称键
        self.suggested_keys: List[str] = []        # 无原文、建议译者手动补写的句子键
        self.untranslatable: List[str] = []        # 多词事件原文等无法覆盖的文本
        self.untranslatable_detail: List[Tuple[str, str]] = []  # (文件, 文本)
        self.inline_caveats: List[str] = []        # 内联优先导致的注意事项
        self.notes: List[str] = []
        self.counts: Dict[str, int] = {}
        self.conversations: List[Tuple[Path, str]] = []  # (源对话文件, 其语言)
        self.event_rewrite_files: List[Path] = []  # --rewrite-events 时待改写的事件文件
        self.vanilla_english: Dict[str, str] = {}  # 原版英文键值（冲突检查）
        self.vanilla_covered = 0                    # 与原版相同而跳过的键数
        self.placeholder_keys: Set[str] = set()     # 值为标识符占位的键

    def add(self, key: str, value: str, group: str, has_source: bool = True) -> bool:
        """登记一个键。返回是否新增。

        - 与原版英文键值完全相同（或无原文但原版已有该键）的键跳过：
          避免在同一语言下与原版译文形成随机二选一。
        - 占位值（无原文，用标识符填充）不覆盖任何已有值；
          真实原文可替换先前的占位值。
        """
        key = key.strip().lower()
        if not key or not valid_key(key):
            return False
        value = norm_value(value)
        van = self.vanilla_english.get(key)
        if van is not None:
            if (has_source and value == van.strip()) or not has_source:
                self.vanilla_covered += 1
                return False
        if key in self.keys:
            if not has_source:
                return False  # 占位不覆盖已有值
            if key in self.placeholder_keys:
                # 真实原文替换占位值（在占位值所在的组内更新）
                self.keys[key] = value
                self.placeholder_keys.discard(key)
                cur_group = self.key_group.get(key)
                for g_label, g_list in self.groups:
                    if g_label == cur_group:
                        for i, (k, _v) in enumerate(g_list):
                            if k == key:
                                g_list[i] = (k, value)
                                break
                        break
                self.no_source_name_keys = [x for x in self.no_source_name_keys if x != key]
                return False
            if self.keys[key] != value and value:
                self.conflicts.append((key, self.keys[key], value))
            return False
        self.keys[key] = value
        self.key_group[key] = group
        if not has_source:
            self.placeholder_keys.add(key)
        if not any(g[0] == group for g in self.groups):
            self.groups.append((group, []))
        for g_label, g_list in self.groups:
            if g_label == group:
                g_list.append((key, value))
                break
        return True

    def has(self, key: str) -> bool:
        return key.strip().lower() in self.keys


# ============================================================================
# 各类型提取器
# ============================================================================

def _resolve_inherited_attrs(elem: ET.Element, by_id: Dict[str, ET.Element],
                             depth: int = 0) -> Dict[str, str]:
    """沿 variantof/inherit 链合并属性（子覆盖父），用于物品/结构变种。"""
    attrs = ci_attrs(elem)
    parent_id = (attrs.get("variantof") or attrs.get("inherit") or "").strip().lower()
    if parent_id and parent_id in by_id and depth < 20:
        parent = _resolve_inherited_attrs(by_id[parent_id], by_id, depth + 1)
        merged = dict(parent)
        merged.update(attrs)
        return merged
    return attrs


def extract_item_like(root: ET.Element, label: str, res: ExtractResult) -> None:
    """Item / Structure / ItemAssembly 注册文件 → entityname / entitydescription。"""
    elems = [e for e in root.iter() if tag_lower(e) in ("item", "structure")]
    by_id: Dict[str, ET.Element] = {}
    for e in elems:
        ident = ci_get(e, "identifier", "").strip().lower()
        if ident:
            by_id.setdefault(ident, e)

    n_name = n_desc = 0
    for e in elems:
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        attrs = _resolve_inherited_attrs(e, by_id)
        own_attrs = ci_attrs(e)
        is_variant = bool((own_attrs.get("variantof") or own_attrs.get("inherit") or "").strip())
        ident_l = ident.lower()

        nameid = (attrs.get("nameidentifier") or "").strip()
        fallback_id = (attrs.get("fallbacknameidentifier") or "").strip()
        name = (attrs.get("name") or "").strip()
        descid = (attrs.get("descriptionidentifier") or "").strip()
        desc = (attrs.get("description") or "").strip()
        # description 也可以是 <Description> 子元素（仅看本元素）
        if not desc:
            for c in e:
                if tag_lower(c) == "description" and c.text:
                    desc = c.text.strip()
                    break

        # 名称键（键优先于内联 name，物品/结构可直接被汉化包覆盖）
        name_key_id = (nameid or ident).lower()
        if name:
            if res.add("entityname." + name_key_id, name, label):
                n_name += 1
        elif not is_variant:
            # 非变种且无任何原文：用标识符占位（当前游戏同样显示标识符或空白）
            if res.add("entityname." + name_key_id, ident, label, has_source=False):
                n_name += 1
                res.no_source_name_keys.append("entityname." + name_key_id)
        # 种变种且无原文：跳过（当前会显示基类名称，不破坏），列入手动建议
        elif is_variant and not name:
            res.suggested_keys.append("entityname." + name_key_id + f"（变种 {ident_l}，原文来自基类）")
        if fallback_id and name:
            res.add("entityname." + fallback_id.lower(), name, label)
        # 描述键（有原文才输出，避免空值键把内联描述挤成空白）
        desc_key_id = (descid or nameid or ident).lower()
        if desc:
            if res.add("entitydescription." + desc_key_id, desc, label):
                n_desc += 1
        elif not is_variant:
            res.suggested_keys.append("entitydescription." + desc_key_id)
        # <Fabricable displayname="xxx"> → displayname.xxx（制造配方显示名）
        for c in e.iter():
            if tag_lower(c) in ("fabricable", "fabricationdescription"):
                dn = ci_get(c, "displayname", "").strip()
                if dn and valid_key(dn):
                    res.add("displayname." + dn.lower(), dn, label, has_source=False)
    res.counts["物品与结构名称"] = res.counts.get("物品与结构名称", 0) + n_name
    res.counts["物品与结构描述"] = res.counts.get("物品与结构描述", 0) + n_desc


def extract_characters(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        t = tag_lower(e)
        if t == "character":
            species = (ci_get(e, "speciesname") or ci_get(e, "name") or "").strip()
            override = ci_get(e, "speciestranslationoverride", "").strip()
            displayname = ci_get(e, "displayname", "").strip()
            key_id = (override or species).lower()
            if species and key_id:
                if res.add("character." + key_id, species, label, has_source=False):
                    n += 1
                    res.no_source_name_keys.append("character." + key_id)
            if displayname:
                res.inline_caveats.append(
                    f"角色 {species or '?'} 使用内联 DisplayName=\"{displayname}\"，"
                    f"文本键无法覆盖（如需翻译请改模组 XML）")
        elif t == "charactervariant":
            species = ci_get(e, "speciesname", "").strip()
            override = ci_get(e, "speciestranslationoverride", "").strip()
            key_id = (override or species).lower()
            if species and key_id and not override:
                if res.add("character." + key_id, species, label, has_source=False):
                    n += 1
                    res.no_source_name_keys.append("character." + key_id)
    res.counts["角色"] = res.counts.get("角色", 0) + n


def extract_afflictions(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        if tag_lower(e) != "affliction":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        tid = (ci_get(e, "translationoverride", "") or ident).strip().lower()
        name = ci_get(e, "name", "").strip()
        desc = ci_get(e, "description", "").strip()
        cod = ci_get(e, "causeofdeathdescription", "").strip()
        codself = ci_get(e, "selfcauseofdeathdescription", "").strip()
        if res.add("afflictionname." + tid, name or ident, label, has_source=bool(name)):
            n += 1
            if not name:
                res.no_source_name_keys.append("afflictionname." + tid)
        if desc:
            res.add("afflictiondescription." + tid, desc, label)
        else:
            res.suggested_keys.append("afflictiondescription." + tid)
        if cod:
            res.add("afflictioncauseofdeath." + tid, cod, label)
        if codself:
            res.add("afflictioncauseofdeathself." + tid, codself, label)
        nameid = ci_get(e, "nameidentifier", "").strip()
        if nameid and valid_key(nameid):
            res.add(nameid.lower(), name or nameid, label)
    res.counts["疾病"] = res.counts.get("疾病", 0) + n


def extract_jobs(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        if tag_lower(e) != "job":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        if res.add("jobname." + ident.lower(), ident, label, has_source=False):
            n += 1
            res.no_source_name_keys.append("jobname." + ident.lower())
        res.suggested_keys.append("jobdescription." + ident.lower())
    res.counts["职业"] = res.counts.get("职业", 0) + n


def extract_missions(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        if tag_lower(e) != "mission":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        tid = (ci_get(e, "textidentifier", "") or ident).strip().lower()
        name = ci_get(e, "name", "").strip()
        desc = ci_get(e, "description", "").strip()
        if res.add("missionname." + tid, name or ident, label, has_source=bool(name)):
            n += 1
            if not name:
                res.no_source_name_keys.append("missionname." + tid)
        if desc:
            res.add("missiondescription." + tid, desc, label)
        else:
            res.suggested_keys.append("missiondescription." + tid)
        # 头衔/消息/成败/声呐标签：MissionPrefab 仅从文本键取值，无内联原文
        #（模组自带英文包有这些键时已在转出阶段覆盖）
        for k in ("missionheader0." + tid, "missionmessage0." + tid,
                  "missionsuccess." + tid, "missionfailure." + tid):
            res.suggested_keys.append(k)
        if ci_get(e, "sonarlabel", "").strip():
            res.suggested_keys.append("missionsonarlabel." + tid)
    res.counts["任务"] = res.counts.get("任务", 0) + n


def extract_upgrades(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    inline_upgrade_notes: List[str] = []
    for e in root.iter():
        t = tag_lower(e)
        if t == "upgrademodule":
            ident = ci_get(e, "identifier", "").strip()
            if not ident:
                continue
            nameid = ci_get(e, "nameidentifier", "").strip()
            descid = ci_get(e, "descriptionidentifier", "").strip()
            name = ci_get(e, "name", "").strip()
            desc = ci_get(e, "description", "").strip()
            if res.add("upgradename." + (nameid or ident).lower(), name or ident, label,
                       has_source=bool(name)):
                n += 1
                if not name:
                    res.no_source_name_keys.append("upgradename." + (nameid or ident).lower())
            if desc:
                res.add("upgradedescription." + (descid or ident).lower(), desc, label)
            if name and not nameid:
                inline_upgrade_notes.append(ident)
        elif t == "upgradecategory":
            ident = ci_get(e, "identifier", "").strip()
            if not ident:
                continue
            nameid = ci_get(e, "nameidentifier", "").strip()
            name = ci_get(e, "name", "").strip()
            if nameid and valid_key(nameid):
                res.add(nameid.lower(), name or nameid, label)
            else:
                if res.add("upgradecategory." + ident.lower(), name or ident, label):
                    n += 1
            if name and not nameid:
                inline_upgrade_notes.append(ident + "（分类）")
    if inline_upgrade_notes:
        res.inline_caveats.append(
            "升级模块/分类内联 name 优先于文本键，汉化包无法直接覆盖: "
            + ", ".join(inline_upgrade_notes[:8])
            + ("…" if len(inline_upgrade_notes) > 8 else "")
            + "（--mode inplace 会自动补 nameidentifier 修复）")
    res.counts["升级"] = res.counts.get("升级", 0) + n


def extract_talents(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        if tag_lower(e) != "talent":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        nameid = ci_get(e, "nameidentifier", "").strip()
        if nameid and valid_key(nameid):
            res.add(nameid.lower(), nameid, label, has_source=False)
        else:
            if res.add("talentname." + ident.lower(), ident, label, has_source=False):
                n += 1
                res.no_source_name_keys.append("talentname." + ident.lower())
        if ci_get(e, "description", "").strip():
            # 描述属性存在时 talentdescription 键不会被查询——只能提示
            res.suggested_keys.append("talentdescription." + ident.lower() + "（描述为内联属性，键不生效）")
        else:
            res.suggested_keys.append("talentdescription." + ident.lower())
    res.counts["天赋"] = res.counts.get("天赋", 0) + n


def extract_factions(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    inline_factions: List[str] = []
    for e in root.iter():
        if tag_lower(e) != "faction":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        name = ci_get(e, "name", "").strip()
        desc = ci_get(e, "description", "").strip()
        shortdesc = ci_get(e, "shortdescription", "").strip()
        if res.add("faction." + ident.lower(), name or ident, label, has_source=bool(name)):
            n += 1
            if not name:
                res.no_source_name_keys.append("faction." + ident.lower())
        if desc:
            res.add("faction." + ident.lower() + ".description", desc, label)
        if shortdesc:
            res.add("faction." + ident.lower() + ".shortdescription", shortdesc, label)
        if name:
            inline_factions.append(ident)
    if inline_factions:
        res.inline_caveats.append(
            "阵营内联 name/description 优先于文本键，汉化包无法覆盖: "
            + ", ".join(inline_factions[:8])
            + ("…" if len(inline_factions) > 8 else "")
            + "（--mode inplace 会把内联值移入文本键修复）")
    res.counts["阵营"] = res.counts.get("阵营", 0) + n


def extract_container_tags(root: ET.Element, label: str, res: ExtractResult) -> None:
    n = 0
    for e in root.iter():
        if tag_lower(e) != "containertag":
            continue
        ident = ci_get(e, "identifier", "").strip()
        if not ident:
            continue
        nameid = ci_get(e, "nameidentifier", "").strip()
        key_id = (nameid or ident).lower()
        if res.add("tagname." + key_id, ident, label, has_source=False):
            n += 1
            res.no_source_name_keys.append("tagname." + key_id)
        res.suggested_keys.append("tagdescription." + key_id)
        suffix = ci_get(e, "suffix", "").strip()
        if suffix and valid_key(suffix):
            res.suggested_keys.append(suffix.lower() + ".tagnamesuffix")
    res.counts["物品标签"] = res.counts.get("物品标签", 0) + n


# ---------------------------------------------------------------------------
# 事件文本（对照官方 dumpeventtexts 逻辑，DebugConsole.cs）
# ---------------------------------------------------------------------------

def _looks_like_keyref(text: str) -> bool:
    """无空格且含点号 → 很可能是文本键引用（如 entityname.foo）。"""
    return (" " not in text) and ("." in text)


def collect_event_texts(elem: ET.Element, src_file: str, res: ExtractResult,
                        known_keys: Set[str], vanilla_keys: Set[str]) -> None:
    """递归收集事件文本（非破坏性，供 separate 模式）。

      - text 值以 EventText./Tutorial. 开头、来自 <Text tag=...>、或形如键引用（无空格含点）:
        值应来自模组自带文本文件（转出阶段已覆盖）；未见定义时记入悬空引用。
      - 单词原文（无空白、合法元素名、不与原版键冲突）→ 以其本身为键输出。
      - 多词原文 → 汉化包无法覆盖（XML 元素名不允许空格），记入 untranslatable。
    """
    text_val = ci_get(elem, "text", "").strip()
    is_tag_attr = False
    if not text_val:
        for c in elem:
            if tag_lower(c) == "text":
                tg = ci_get(c, "tag", "").strip()
                if tg:
                    text_val = tg
                    is_tag_attr = True
                break

    if text_val:
        low = text_val.lower()
        if is_tag_attr or low.startswith("eventtext.") or low.startswith("tutorial.") or _looks_like_keyref(text_val):
            if low not in known_keys and not res.has(low) and low not in vanilla_keys:
                res.suggested_keys.append(low + "（事件引用但未见定义）")
        elif " " in text_val or "\n" in text_val or not valid_key(text_val) or low in vanilla_keys:
            res.untranslatable.append(text_val)
            res.untranslatable_detail.append((src_file, text_val))
        else:
            # 单词原文：以其本身为键，翻译后即可覆盖（TextManager 先按标签查找）
            res.add(low, text_val, "事件原文单词键")

    for sub in elem:
        if tag_lower(sub) == "text":
            continue  # 官方逻辑同样跳过
        collect_event_texts(sub, src_file, res, known_keys, vanilla_keys)


def extract_events(root: ET.Element, src_file: str, label: str, res: ExtractResult,
                   known_keys: Set[str], vanilla_keys: Set[str]) -> None:
    n = 0
    # <EventPrefabs> 的子元素 = 事件 prefab（<Event>/<TraitorEvent>）
    for container in root.iter():
        if tag_lower(container) != "eventprefabs":
            continue
        for ev in container:
            evid = ci_get(ev, "identifier", "").strip()
            if not evid:
                continue
            if res.add("eventname." + evid.lower(), evid, label, has_source=False):
                n += 1
                res.no_source_name_keys.append("eventname." + evid.lower())
            collect_event_texts(ev, src_file, res, known_keys, vanilla_keys)
    res.counts["事件"] = res.counts.get("事件", 0) + n


# ---------------------------------------------------------------------------
# 文本包（模组自带 <Text> 文件）解析与转出
# ---------------------------------------------------------------------------

def parse_text_pack(root: ET.Element) -> Dict[str, str]:
    """解析 infotexts 根 → {key小写: value}。兼容 <override> 包裹。"""
    out: Dict[str, str] = {}

    def walk(el: ET.Element):
        for c in el:
            t = tag_lower(c)
            if t == "override":
                walk(c)
                continue
            if t:
                val = (c.text or "")
                # 递归取全部内部文本（值里不应有子元素，稳妥起见拼接）
                if len(c) > 0:
                    val = "".join(c.itertext())
                out.setdefault(t, val.strip())

    walk(root)
    return out


def extract_pack_passthrough(pack: Dict[str, str], lang_display: str, label: str,
                             res: ExtractResult) -> int:
    """把模组自带（源语言）文本包整包转出。与原版英文相同的键在 add() 内跳过。"""
    n = 0
    for k, v in pack.items():
        if not valid_key(k):
            continue
        if res.add(k, v, label):
            n += 1
    return n


# ---------------------------------------------------------------------------
# NPC 对话文件
# ---------------------------------------------------------------------------

_CONV_ROOT_RE = re.compile(r"<(?![?!])[A-Za-z_]")


def conversation_language(root: ET.Element) -> str:
    return ci_get(root, "language", "English").strip()


def _find_root_tag_span(raw: str) -> Optional[Tuple[int, int]]:
    """定位根元素开标签的文本区间（跳过 <?...?> 与 <!--...-->）。"""
    m = _CONV_ROOT_RE.search(raw)
    if not m:
        return None
    i = m.start()
    # 从 '<' 开始扫描到开标签结束的 '>'，跳过引号内的 '>'
    j = i + 1
    in_quote = None
    while j < len(raw):
        ch = raw[j]
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == ">":
            return (i, j + 1)
        j += 1
    return None


def convert_conversation_text(raw: str, canonical_lang: str, nowhitespace: str) -> str:
    """对话文件 → 目标语言副本：仅改根元素 language/nowhitespace 属性，其余原样保留。"""
    span = _find_root_tag_span(raw)
    if span is None:
        return raw
    s, e = span
    tag = raw[s:e]
    m = re.match(r"<([A-Za-z_][\w.\-:]*)", tag)
    tag_name = m.group(1)
    attrs = tag[m.end():-1]  # 去掉结尾的 ">"（保留自闭合的 "/"）
    # language 属性
    lang_re = re.compile(r"(\blanguage\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE)
    if lang_re.search(attrs):
        new_attrs = lang_re.sub(lambda mm: mm.group(1) + mm.group(2) + canonical_lang + mm.group(2), attrs, count=1)
    else:
        new_attrs = f' language="{canonical_lang}"' + attrs
    ws_re = re.compile(r"(\bnowhitespace\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE)
    if ws_re.search(new_attrs):
        new_attrs = ws_re.sub(lambda mm: mm.group(1) + mm.group(2) + nowhitespace + mm.group(2), new_attrs, count=1)
    else:
        new_attrs += f' nowhitespace="{nowhitespace}"'
    return raw[:s] + f"<{tag_name}{new_attrs}" + raw[e - 1:]


# ============================================================================
# 单模组处理管线
# ============================================================================

class ModJob:
    def __init__(self, mod_root: Path, opts: argparse.Namespace):
        self.mod_root = mod_root
        self.opts = opts
        self.fl: Optional[FileList] = None
        self.res = ExtractResult()
        self.existing_langs: List[str] = []
        self.source_pack: Optional[Dict[str, str]] = None
        self.source_lang: Optional[str] = None
        self.skip_reason: Optional[str] = None
        self.output_desc: List[str] = []


def analyze_mod(job: ModJob, cross_mod_lookup, vanilla_english: Dict[str, str]) -> None:
    opts, res = job.opts, job.res
    fl = parse_filelist(job.mod_root, cross_mod_lookup)
    job.fl = fl
    if fl.error:
        job.skip_reason = fl.error
        return
    target_key = opts.target_lang_key

    # ---- 1. 模组自带文本包：语言检测 + 源语言包收集 ----
    packs_by_lang: Dict[str, Dict[str, str]] = {}
    for e in fl.entries:
        if e.ctype == "text" and e.exists:
            root = load_xml_file(e.path)
            if root is None:
                fl.warnings.append(f"文本文件解析失败: {e.raw_path}")
                continue
            lang = ci_get(root, "language", "English").strip() or "English"
            packs_by_lang.setdefault(lang.lower(), {})
            packs_by_lang[lang.lower()].update(parse_text_pack(root))
    # ---- 2. NPC 对话文件语言 ----
    conv_langs: Set[str] = set()
    for e in fl.entries:
        if e.ctype == "npcconversations" and e.exists:
            root = load_xml_file(e.path)
            if root is not None:
                conv_langs.add(conversation_language(root).lower())
                res.conversations.append((e.path, conversation_language(root)))
    job.existing_langs = sorted(packs_by_lang.keys() | conv_langs)
    present = [l for l in opts.target_lang_keys if l in packs_by_lang or l in conv_langs]
    if present and not opts.force:
        names = ", ".join(LANGUAGES[l][0] if l in LANGUAGES else l for l in present)
        job.skip_reason = f"已有目标语言 {names} 的本地化（--force 可强制）"
        return
    # 源语言包：优先英文，其次任一非目标语言
    if "english" in packs_by_lang:
        job.source_pack, job.source_lang = packs_by_lang["english"], "English"
    else:
        for lk, pk in packs_by_lang.items():
            if lk != target_key:
                job.source_pack, job.source_lang = pk, lk
                break

    # 原版键（英文全集用于冲突跳过；事件单词键用 英文∪目标语言 做封锁表）
    res.vanilla_english = vanilla_english
    vanilla_union: Set[str] = set(vanilla_english.keys())
    if opts.vanilla_target_keys:
        vanilla_union.update(opts.vanilla_target_keys.keys()
                             if isinstance(opts.vanilla_target_keys, dict)
                             else opts.vanilla_target_keys)

    known_keys: Set[str] = set(job.source_pack.keys()) if job.source_pack else set()

    # ---- 3. 源语言文本包整包转出 ----
    if job.source_pack is not None:
        label = f"模组自带文本转出（{job.source_lang}）"
        n = extract_pack_passthrough(job.source_pack, job.source_lang, label, res)
        res.counts["文本文件转出"] = n

    # ---- 4. 按注册类型提取 ----
    skip_types: Set[str] = set()
    for e in fl.entries:
        if not e.exists:
            continue
        ctype = e.ctype
        if ctype in ("item", "structure", "itemassembly"):
            root = load_xml_file(e.path)
            if root is not None:
                extract_item_like(root, f"物品与结构: {e.raw_path}", res)
        elif ctype == "character":
            root = load_xml_file(e.path)
            if root is not None:
                extract_characters(root, f"角色: {e.raw_path}", res)
        elif ctype == "afflictions":
            root = load_xml_file(e.path)
            if root is not None:
                extract_afflictions(root, f"疾病: {e.raw_path}", res)
        elif ctype == "jobs":
            root = load_xml_file(e.path)
            if root is not None:
                extract_jobs(root, f"职业: {e.raw_path}", res)
        elif ctype == "missions":
            root = load_xml_file(e.path)
            if root is not None:
                extract_missions(root, f"任务: {e.raw_path}", res)
        elif ctype == "upgrademodules":
            root = load_xml_file(e.path)
            if root is not None:
                extract_upgrades(root, f"升级: {e.raw_path}", res)
        elif ctype == "talents":
            root = load_xml_file(e.path)
            if root is not None:
                extract_talents(root, f"天赋: {e.raw_path}", res)
        elif ctype == "factions":
            root = load_xml_file(e.path)
            if root is not None:
                extract_factions(root, f"阵营: {e.raw_path}", res)
        elif ctype == "containertag":
            root = load_xml_file(e.path)
            if root is not None:
                extract_container_tags(root, f"物品标签: {e.raw_path}", res)
        elif ctype == "randomevents":
            if opts.no_events:
                skip_types.add("randomevents")
            else:
                root = load_xml_file(e.path)
                if root is not None:
                    extract_events(root, e.raw_path, f"事件: {e.raw_path}", res,
                                   known_keys, vanilla_union)
                    res.event_rewrite_files.append(e.path)
        elif ctype in ("text", "npcconversations", "submarine", "wreck", "enemysubmarine",
                       "beaconstation", "other", "none", "uistyle", "sounds", "particles",
                       "serverexecutable", "backgroundcreatureprefabs"):
            pass  # 已处理或无可译文本
        else:
            skip_types.add(ctype)
    if skip_types:
        res.notes.append("未提取（无标准可译键）的类型: " + ", ".join(sorted(skip_types)))

    # 冲突提示
    if res.conflicts:
        sample = "; ".join(f"{k}: {a!r}≠{b!r}" for k, a, b in res.conflicts[:5])
        res.notes.append(f"有 {len(res.conflicts)} 个键在不同位置值不同，已保留首个: {sample}")


# ============================================================================
# 输出
# ============================================================================

def build_full_infotexts_text(res: ExtractResult, lang_display: str, nowhitespace: str,
                              translatedname: str, use_override: bool) -> str:
    """生成全新的 infotexts XML（写入不存在的文件时用）。"""
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             f'<infotexts language="{xml_escape_text(lang_display)}" '
             f'nowhitespace="{nowhitespace}" '
             f'translatedname="{xml_escape_text(translatedname)}">']
    for label, entries in res.groups:
        if not entries:
            continue
        lines.append("")
        lines.append(f"  <!-- {label} -->")
        if use_override:
            lines.append("  <override>")
        for key, value in entries:
            lines.append(f"  <{key}>{xml_escape_text(value)}</{key}>")
        if use_override:
            lines.append("  </override>")
    lines.append("</infotexts>")
    return "\r\n".join(lines) + "\r\n"


def write_infotexts_file(path: Path, res: ExtractResult, opts, use_override: bool) -> Tuple[int, bool]:
    """写出本地化文件。

    - 文件不存在：写入全部键，返回 (键数, True)。
    - 文件已存在（重复运行保护译稿）：只把缺失的键追加到 </infotexts> 前，
      已有内容原样保留，返回 (新增键数, False)。
    - opts.dry_run 时只统计不写盘。
    """
    existing = None
    if path.is_file():
        root = load_xml_file(path)
        if root is not None:
            lang = ci_get(root, "language", "").strip().lower()
            if lang and lang != opts.target_lang_key:
                opts.log(f"  警告: {path} 已存在且语言为 {lang}（目标 {opts.target_lang_key}），"
                         f"不合并不覆盖")
                return 0, False
            existing = parse_text_pack(root)
        else:
            opts.log(f"  警告: {path} 已存在但解析失败，保留不动")
            return 0, False

    if existing is None:
        text = build_full_infotexts_text(res, opts.target_lang_display, opts.target_nowhitespace,
                                         opts.target_translatedname, use_override)
        if not opts.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(text)
        return len(res.keys), True

    # 合并：仅追加缺失键
    new_entries = [(k, v) for k, v in sorted(res.keys.items()) if k not in existing]
    if not new_entries:
        return 0, False
    if opts.dry_run:
        return len(new_entries), False
    raw = read_text_robust(path)
    nl = "\r\n" if "\r\n" in raw else "\n"
    block_lines = ["<!-- 以下为本轮新增键 -->"]
    if use_override:
        block_lines.append("<override>")
    for k, v in new_entries:
        block_lines.append(f"<{k}>{xml_escape_text(v)}</{k}>")
    if use_override:
        block_lines.append("</override>")
    insert = nl.join("  " + l for l in block_lines)
    raw, ok = _insert_line_before(raw, r"</infotexts\s*>", insert)
    if not ok:
        opts.log(f"  警告: {path} 缺少 </infotexts>，无法追加，保留不动")
        return 0, False
    with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
        f.write(raw)
    return len(new_entries), False


def write_conversation_copy(src: Path, dst: Path, opts) -> bool:
    """生成目标语言对话副本；已存在则保留译稿返回 False。dry-run 只报告。"""
    if dst.is_file():
        return False
    if not opts.dry_run:
        raw = read_text_robust(src)
        converted = convert_conversation_text(raw, opts.target_lang_display, opts.target_nowhitespace)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(str(dst), "w", encoding="utf-8-sig", newline="") as f:
            f.write(converted)
    return True


def make_filelist_text(pkg_name: str, gameversion: str, entries: List[Tuple[str, str]]) -> str:
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    attrs = f'name="{xml_escape_text(pkg_name)}" modversion="1.0.0" corepackage="False"'
    if gameversion:
        attrs += f' gameversion="{xml_escape_text(gameversion)}"'
    lines.append(f"<contentpackage {attrs}>")
    for ename, rel in entries:
        lines.append(f'  <{ename} file="%ModDir%/{rel}" />')
    lines.append("</contentpackage>")
    return "\r\n".join(lines) + "\r\n"


def lang_no_ws(lang_display: str) -> str:
    return lang_display.replace(" ", "")


# ---------------------------------------------------------------------------
# separate 模式
# ---------------------------------------------------------------------------

def apply_separate(job: ModJob, out_root: Path, pkg_root: Optional[Path] = None,
                   name_prefix: str = "",
                   shared_entries: Optional[List[Tuple[str, str]]] = None,
                   shared_gameversion: List[str] = None) -> None:
    """separate 模式输出。

    - 每模组一个汉化包（默认）：pkg_root 为该包目录，写 filelist.xml。
    - --single-package：shared_entries 提供时，各模组文件写入统一目录结构
      （Text_<模组名>/...），条目汇集到 shared_entries，由调用方统一写 filelist。
    """
    opts, fl, res = job.opts, job.fl, job.res
    if pkg_root is None:
        pkg_root = out_root / sanitize_filename(f"{fl.name} {opts.modname_suffix}")
    lang_ws = lang_no_ws(opts.target_lang_display)
    mod_name_sanitized = sanitize_filename(fl.name)
    # shared 模式下条目路径须包含 Text_<模组名> 子目录（相对于合并包根）
    base_rel = (pkg_root.name + "/") if shared_entries is not None else ""

    # 文本文件
    text_rel = f"{base_rel}Localization/{lang_ws}/{mod_name_sanitized}{lang_ws}.xml"
    text_abs = pkg_root / ("Localization/" + lang_ws + f"/{mod_name_sanitized}{lang_ws}.xml")
    added, _ = write_infotexts_file(text_abs, res, opts, use_override=opts.force)
    job.output_desc.append(f"文本 {added} 键 → {text_abs}")

    entries: List[Tuple[str, str]] = [("Text", text_rel)]

    # 对话文件
    if not opts.no_conversations:
        for src_conv, _lang in res.conversations:
            if _lang.lower() in opts.target_lang_keys:
                continue  # 已有目标语言对话
            stem = sanitize_filename((name_prefix + "_" if name_prefix else "") + src_conv.stem)
            rel = f"{base_rel}Localization/{lang_ws}/{stem}_{lang_ws}.xml"
            dst = pkg_root / ("Localization/" + lang_ws + f"/{stem}_{lang_ws}.xml")
            if write_conversation_copy(src_conv, dst, opts):
                entries.append(("NPCConversations", rel))
                job.output_desc.append(f"对话副本 → {dst}")

    if opts.dry_run:
        job.output_desc.append(f"汉化包目录（dry-run 未写入）: {pkg_root}")
        return

    if shared_entries is not None:
        # 合并模式：条目上交，不写各自 filelist
        shared_entries.extend(entries)
        if shared_gameversion is not None and fl.gameversion:
            shared_gameversion.append(fl.gameversion)
        return

    # filelist.xml（已存在则合并条目）
    fl_path = pkg_root / "filelist.xml"
    if fl_path.is_file():
        raw = read_text_robust(fl_path)
        changed = False
        for ename, rel in entries:
            pat = re.compile(r"<" + ename + r"\b[^>]*file\s*=\s*[\"']%ModDir%/" + re.escape(rel) + r"[\"']", re.IGNORECASE)
            if not pat.search(raw):
                raw, ok = _insert_entry_into_filelist(raw, ename, rel)
                changed = changed or ok
        if changed:
            with open(str(fl_path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(raw)
    else:
        text = make_filelist_text(f"{fl.name} {opts.modname_suffix}", fl.gameversion, entries)
        pkg_root.mkdir(parents=True, exist_ok=True)
        with open(str(fl_path), "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)


def _insert_line_before(raw: str, close_tag_re: str, line: str) -> Tuple[str, bool]:
    """在闭合标签所在行之前插入一行（保持换行风格）。"""
    m = re.search(close_tag_re, raw, re.IGNORECASE)
    if not m:
        return raw, False
    nl = "\r\n" if "\r\n" in raw else "\n"
    pos = m.start()
    # 找闭合标签前最后一个换行，插到它后面（保证独立成行）
    insert_at = -1
    for i in range(pos - 1, -1, -1):
        if raw[i] == "\n":
            insert_at = i + 1
            break
    if insert_at < 0:
        insert_at = pos
        return raw[:insert_at] + line + nl + raw[insert_at:], True
    return raw[:insert_at] + line + nl + raw[insert_at:], True


def _insert_entry_into_filelist(raw: str, ename: str, rel: str) -> Tuple[str, bool]:
    """在 </contentpackage> 前插入一行 <Text>/<NPCConversations> 条目。"""
    return _insert_line_before(raw, r"</contentpackage\s*>",
                               f'  <{ename} file="%ModDir%/{rel}" />')


# ---------------------------------------------------------------------------
# inplace 模式
# ----------------------------------------------------------------===========

def apply_inplace(job: ModJob) -> None:
    opts, fl, res = job.opts, job.fl, job.res
    mod_root = job.mod_root

    # 重新解析 filelist（多语言时包含此前语言已插入的条目）
    fl_fresh = parse_filelist(mod_root)
    if not fl_fresh.error:
        job.fl = fl = fl_fresh

    # 0. 修改前先按原 filelist 复算哈希（后续内容文件会被改动）
    entries = [(e.ctype, e.path) for e in fl.entries]
    original_hash_ok = None
    if fl.expectedhash:
        recomputed = package_hash(entries, fl.name, fl.modversion)
        original_hash_ok = (recomputed.casefold() == fl.expectedhash.casefold())

    # 1. 事件标签化改写（--rewrite-events）
    if opts.rewrite_events:
        if opts.dry_run:
            n_texts = count_rewrite_texts(job)
            job.output_desc.append(f"事件标签化改写（dry-run 未写入）: 约 {n_texts} 处文本")
        else:
            n_files, n_texts = rewrite_event_files(job)
            job.output_desc.append(f"事件标签化改写: {n_files} 个文件 / {n_texts} 处文本")
            if res.untranslatable:
                res.untranslatable = []
                res.untranslatable_detail = []

    # 2. 升级/阵营内联修复（默认开启）
    if opts.fix_inline and not opts.dry_run:
        n = fix_inline_names(job)
        if n:
            job.output_desc.append(f"内联名称修复: {n} 处")

    # 3. 写 Localization 文件
    lang_ws = lang_no_ws(opts.target_lang_display)
    mod_name_sanitized = sanitize_filename(fl.name)
    text_rel = f"Localization/{lang_ws}/{mod_name_sanitized}{lang_ws}.xml"
    text_abs = mod_root / text_rel
    added, _ = write_infotexts_file(text_abs, res, opts, use_override=False)
    job.output_desc.append(f"文本 {added} 键 → {text_abs}")

    fl_entries: List[Tuple[str, str]] = [("Text", text_rel)]
    if not opts.no_conversations:
        for src_conv, _lang in res.conversations:
            if _lang.lower() in opts.target_lang_keys:
                continue
            stem = sanitize_filename(src_conv.stem)
            rel = f"Localization/{lang_ws}/{stem}_{lang_ws}.xml"
            if write_conversation_copy(src_conv, mod_root / rel, opts):
                fl_entries.append(("NPCConversations", rel))
                job.output_desc.append(f"对话副本 → {mod_root / rel}")

    if opts.dry_run:
        return

    # 4. 修改 filelist.xml（先备份 → 插入条目 → 重算哈希）
    fl_path = mod_root / "filelist.xml"
    bak = mod_root / "filelist.xml.bak"
    if not bak.is_file():
        import shutil
        shutil.copy2(str(fl_path), str(bak))
    raw = read_text_robust(fl_path)
    for ename, rel in fl_entries:
        pat = re.compile(r"<" + ename + r"\b[^>]*file\s*=\s*[\"']%ModDir%/" + re.escape(rel) + r"[\"']", re.IGNORECASE)
        if not pat.search(raw):
            raw, _ok = _insert_entry_into_filelist(raw, ename, rel)

    # 5. 重算 expectedhash
    # gameversion < 1.1.0.0 时游戏不校验哈希（MinimumHashCompatibleVersion），保持原值不动
    hash_check_enabled = True
    try:
        vt = tuple(int(x) for x in fl.gameversion.split("."))
        if vt < (1, 1, 0, 0):
            hash_check_enabled = False
    except ValueError:
        pass
    hash_note = ""
    if fl.expectedhash and hash_check_enabled:
        if original_hash_ok:
            new_entries: List[Tuple[str, Optional[Path]]] = [(e.ctype, e.path) for e in fl.entries]
            for ename, rel in fl_entries:
                ctype = "text" if ename == "Text" else "npcconversations"
                new_entries.append((ctype, resolve_content_path("%ModDir%/" + rel, mod_root)))
            new_hash = package_hash(new_entries, fl.name, fl.modversion)
            raw, _ok = _replace_attr(raw, "expectedhash", new_hash)
            hash_note = f"expectedhash 已重算 → {new_hash}"
        else:
            raw, _ok = _replace_attr(raw, "expectedhash", None)
            hash_note = ("警告: 修改前哈希复算与原值不符（文件在安装后被改动过，或存在跨模组路径），"
                         "已移除 expectedhash 属性以保证模组可加载（游戏对空值不校验）")
    with open(str(fl_path), "w", encoding="utf-8-sig", newline="") as f:
        f.write(raw)
    if hash_note:
        job.output_desc.append(hash_note)
    job.output_desc.append(f"filelist 备份: {bak}")


def _replace_attr(raw: str, attr: str, value: Optional[str]) -> Tuple[str, bool]:
    """替换/删除根元素（第一个标签）的属性。value=None 表示删除。"""
    m = re.search(r"<contentpackage\b[^>]*>", raw, re.IGNORECASE)
    if not m:
        return raw, False
    tag = m.group(0)
    attr_re = re.compile(r"(\s" + re.escape(attr) + r"\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE)
    if value is None:
        new_tag = attr_re.sub("", tag, count=1)
    elif attr_re.search(tag):
        new_tag = attr_re.sub(lambda mm: mm.group(1) + mm.group(2) + value + mm.group(2), tag, count=1)
    else:
        # 属性不存在时补在标签尾部
        if tag.endswith("/>"):
            new_tag = tag[:-2].rstrip() + f' {attr}="{value}" />'
        else:
            new_tag = tag[:-1].rstrip() + f' {attr}="{value}">'
    if new_tag == tag:
        return raw, False
    return raw[:m.start()] + new_tag + raw[m.end():], True


def fix_inline_names(job: ModJob) -> int:
    """升级模块补 nameidentifier/descriptionidentifier；阵营内联属性移入文本键。
    通过文本手术保持文件其余格式不变。返回修复处数。"""
    opts, fl, res = job.opts, job.fl, job.res
    fixed = 0

    for e in fl.entries:
        if not e.exists:
            continue
        if e.ctype not in ("upgrademodules", "factions"):
            continue
        raw = read_text_robust(e.path)
        root = load_xml_file(e.path)
        if root is None:
            continue
        changed = False

        if e.ctype == "upgrademodules":
            for el in root.iter():
                t = tag_lower(el)
                if t == "upgrademodule":
                    ident = ci_get(el, "identifier", "").strip()
                    name = ci_get(el, "name", "").strip()
                    desc = ci_get(el, "description", "").strip()
                    has_nameid = bool(ci_get(el, "nameidentifier", "").strip())
                    has_descid = bool(ci_get(el, "descriptionidentifier", "").strip())
                    if not ident or has_nameid:
                        continue
                    # 仅当文本键的值与内联一致（或键值为占位）时才加 identifier，
                    # 保证改后显示不变、同时变得可翻译
                    if name:
                        key = "upgradename." + ident.lower()
                        if res.keys.get(key, name) != name:
                            continue
                        raw2, ok = _add_attr_to_element(raw, t, ident, "nameidentifier", ident)
                        if ok:
                            raw = raw2
                            changed = True
                            fixed += 1
                    if desc and not has_descid:
                        key = "upgradedescription." + ident.lower()
                        if res.keys.get(key, desc) == desc:
                            raw2, ok = _add_attr_to_element(raw, t, ident, "descriptionidentifier", ident)
                            if ok:
                                raw = raw2
                                changed = True
                                fixed += 1
                elif t == "upgradecategory":
                    ident = ci_get(el, "identifier", "").strip()
                    name = ci_get(el, "name", "").strip()
                    if not ident or ci_get(el, "nameidentifier", "").strip() or not name:
                        continue
                    raw2, ok = _add_attr_to_element(raw, t, ident, "nameidentifier", ident)
                    if ok:
                        raw = raw2
                        changed = True
                        fixed += 1
                        res.add(ident.lower(), name, f"升级分类修复: {e.raw_path}")

        elif e.ctype == "factions":
            for el in root.iter():
                if tag_lower(el) != "faction":
                    continue
                ident = ci_get(el, "identifier", "").strip()
                if not ident:
                    continue
                name = ci_get(el, "name", "").strip()
                desc = ci_get(el, "description", "").strip()
                shortdesc = ci_get(el, "shortdescription", "").strip()
                if not (name or desc or shortdesc):
                    continue
                # 先确保文本键已有值
                if name:
                    res.add("faction." + ident.lower(), name, f"阵营修复: {e.raw_path}")
                if desc:
                    res.add("faction." + ident.lower() + ".description", desc, f"阵营修复: {e.raw_path}")
                if shortdesc:
                    res.add("faction." + ident.lower() + ".shortdescription", shortdesc, f"阵营修复: {e.raw_path}")
                raw2, ok = _remove_attrs_from_element(raw, "faction", ident,
                                                      [a for a, v in (("name", name), ("description", desc),
                                                                       ("shortdescription", shortdesc)) if v])
                if ok:
                    raw = raw2
                    changed = True
                    fixed += 1

        if changed:
            with open(str(e.path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(raw)
    return fixed


def _find_element_tag_span(raw: str, tag: str, ident: str) -> Optional[Tuple[int, int, str]]:
    """按 identifier 定位元素开标签文本区间。"""
    pat = re.compile(
        r"<" + re.escape(tag) + r"\b[^>]*?identifier\s*=\s*[\"']" + re.escape(ident) + r"[\"'][^>]*>",
        re.IGNORECASE)
    matches = list(pat.finditer(raw))
    if len(matches) != 1:
        return None
    m = matches[0]
    return (m.start(), m.end(), m.group(0))


def _add_attr_to_element(raw: str, tag: str, ident: str, attr: str, value: str) -> Tuple[str, bool]:
    span = _find_element_tag_span(raw, tag, ident)
    if span is None:
        return raw, False
    s, e_, tag_text = span
    if re.search(r"\b" + re.escape(attr) + r"\s*=", tag_text, re.IGNORECASE):
        return raw, False
    if tag_text.endswith("/>"):
        new_tag = tag_text[:-2].rstrip() + f' {attr}="{value}" />'
    else:
        new_tag = tag_text[:-1].rstrip() + f' {attr}="{value}">'
    return raw[:s] + new_tag + raw[e_:], True


def _remove_attrs_from_element(raw: str, tag: str, ident: str, attrs: List[str]) -> Tuple[str, bool]:
    span = _find_element_tag_span(raw, tag, ident)
    if span is None:
        return raw, False
    s, e_, tag_text = span
    new_tag = tag_text
    ok = False
    for a in attrs:
        attr_re = re.compile(r"\s+" + re.escape(a) + r'\s*=\s*"[^"]*"|\s+' + re.escape(a) + r"\s*=\s*'[^']*'",
                             re.IGNORECASE)
        new_tag2 = attr_re.sub("", new_tag, count=1)
        if new_tag2 != new_tag:
            ok = True
            new_tag = new_tag2
    if not ok:
        return raw, False
    return raw[:s] + new_tag + raw[e_:], True


# ---------------------------------------------------------------------------
# 事件标签化改写（--rewrite-events，对照官方 dumpeventtexts，DebugConsole.cs）
# ---------------------------------------------------------------------------

def _escape_attr_value_for_match(value: str, quote: str) -> str:
    """把元素解析后的属性值还原为文件中的转义形式（用于文本手术定位）。"""
    v = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quote == '"':
        v = v.replace('"', "&quot;")
    else:
        v = v.replace("'", "&apos;")
    return v


def _replace_text_attr(raw: str, attr_kind: str, old_value: str, new_value: str) -> Tuple[str, int]:
    """在事件文件原文中把 text="旧值"（或 <Text tag="旧值">）替换为新标签。
    返回 (新原文, 替换次数)。"""
    total = 0
    for quote in ('"', "'"):
        escaped_old = _escape_attr_value_for_match(old_value, quote)
        escaped_new = _escape_attr_value_for_match(new_value, quote)
        if escaped_old == escaped_new:
            continue
        if attr_kind == "text":
            pat = re.compile(r"(\btext\s*=\s*)" + re.escape(quote) + re.escape(escaped_old) + re.escape(quote))
        else:
            pat = re.compile(r"(<[Tt]ext\b[^>]*?\btag\s*=\s*)" + re.escape(quote)
                             + re.escape(escaped_old) + re.escape(quote))
        raw, cnt = pat.subn(lambda m: m.group(1) + quote + escaped_new + quote, raw)
        total += cnt
    return raw, total


def _iter_event_prefabs(root: ET.Element):
    for container in root.iter():
        if tag_lower(container) == "eventprefabs":
            for ev in container:
                evid = ci_get(ev, "identifier", "").strip()
                if evid:
                    yield evid, ev


def _read_event_text(elem: ET.Element) -> Tuple[str, str]:
    """读取元素的文本：优先 text 属性，其次 <Text> 子元素的 tag 属性。
    返回 (文本, 属性类别 text/tag)；无文本时 ("", "text")。"""
    text_val = ci_get(elem, "text", "").strip()
    if text_val:
        return text_val, "text"
    for c in elem:
        if tag_lower(c) == "text":
            tg = ci_get(c, "tag", "").strip()
            if tg:
                return tg, "tag"
            break
    return "", "text"


def rewrite_event_files(job: ModJob) -> Tuple[int, int]:
    """把事件中的未标签化文本改为 EventText.* 标签，并把键与英文原文写入提取结果。

    严格对照官方 dumpeventtexts：
      - 同一原文（全局去重）复用同一个标签；
      - 子元素命名：conversationaction → .cN，eventlogaction → .objectiveN，
        option → 去掉末尾 3 字符后接 .oN；
      - 官方对一切未以 EventText./Tutorial. 开头的文本都会标签化（包括单词与
        键引用），此处同样照做——标签化后均可翻译且不会与原版键冲突。
    """
    res = job.res
    text_to_tag: Dict[str, str] = {}
    used_ids: Set[str] = set()
    n_files = n_texts = 0

    for path in res.event_rewrite_files:
        if not path.is_file():
            continue
        raw = read_text_robust(path)
        root = load_xml_file(path)
        if root is None:
            continue
        ops: List[Tuple[str, str, str]] = []  # (属性类别, 原文, 新标签)

        def walk(elem: ET.Element, parent_name: str) -> None:
            nonlocal n_texts
            text_val, attr_kind = _read_event_text(elem)
            if text_val:
                low = text_val.lower()
                if not low.startswith("eventtext.") and not low.startswith("tutorial."):
                    if text_val in text_to_tag:
                        ops.append((attr_kind, text_val, text_to_tag[text_val]))
                    else:
                        text_id = f"EventText.{parent_name}"
                        used_ids.add(parent_name.lower())
                        text_to_tag[text_val] = text_id
                        ops.append((attr_kind, text_val, text_id))
                        key = text_id.lower()
                        if not res.add(key, text_val, f"事件文本: {path.name}"):
                            # 同一位置多条文本共用同一标签：官方输出重复键，
                            # 游戏从中随机取一条（对话变体），追加重复条目保持该行为
                            if key in res.keys and res.keys[key] != text_val:
                                cur = res.key_group.get(key)
                                for g_label, g_list in res.groups:
                                    if g_label == cur:
                                        g_list.append((key, text_val))
                                        break
                        n_texts += 1
            conv = 1
            obj = 1
            for sub in elem:
                t = tag_lower(sub)
                if t == "text":
                    continue
                child_name = parent_name
                if t == "conversationaction":
                    while f"{parent_name}.c{conv}".lower() in used_ids:
                        conv += 1
                    child_name = f"{parent_name}.c{conv}"
                elif t == "eventlogaction":
                    while f"{parent_name}.objective{obj}".lower() in used_ids:
                        obj += 1
                    child_name = f"{parent_name}.objective{obj}"
                elif t == "option":
                    base = parent_name[:-3] if len(parent_name) > 3 else parent_name
                    while f"{base}.o{conv}".lower() in used_ids:
                        conv += 1
                    child_name = f"{base}.o{conv}"
                walk(sub, child_name)

        for evid, ev in _iter_event_prefabs(root):
            walk(ev, evid.lower())

        if not ops:
            continue
        new_raw = raw
        replaced_all = True
        for attr_kind, old, tag in ops:
            new_raw, cnt = _replace_text_attr(new_raw, attr_kind, old, tag)
            if cnt == 0:
                replaced_all = False
        if new_raw != raw:
            # 写回前确认仍是合法 XML
            if parse_xml_text(new_raw) is None:
                job.opts.log(f"  警告: {path} 标签化后 XML 校验失败，放弃写入该文件")
                continue
            with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(new_raw)
            n_files += 1
            if not replaced_all:
                job.opts.log(f"  警告: {path} 有 {len(ops)} 处计划替换但部分未匹配到原文位置")
    return n_files, n_texts


def count_rewrite_texts(job: ModJob) -> int:
    """dry-run 用：统计将被标签化的文本数（不写入）。"""
    res = job.res
    seen: Set[str] = set()
    n = 0
    for path in res.event_rewrite_files:
        if not path.is_file():
            continue
        root = load_xml_file(path)
        if root is None:
            continue

        def walk(elem: ET.Element):
            nonlocal n
            text_val, _kind = _read_event_text(elem)
            if text_val:
                low = text_val.lower()
                if not low.startswith("eventtext.") and not low.startswith("tutorial.") and text_val not in seen:
                    seen.add(text_val)
                    n += 1
            for sub in elem:
                if tag_lower(sub) != "text":
                    walk(sub)

        for _evid, ev in _iter_event_prefabs(root):
            walk(ev)
    return n


# ============================================================================
# 报告
# ============================================================================

def print_mod_report(job: ModJob, idx: int, total: int) -> None:
    fl, res = job.fl, job.res
    wid = fl.steamworkshopid if fl else ""
    header = f"[{idx}/{total}] {fl.name if fl else job.mod_root.name}" + (f" (workshop {wid})" if wid else "")
    print("=" * 78)
    print(header)
    if job.skip_reason:
        print(f"  跳过: {job.skip_reason}")
        return
    print(f"  已有语言: {', '.join(job.existing_langs) or '（无文本文件）'}")
    for k in sorted(res.counts):
        print(f"  {k}: {res.counts[k]}")
    total_keys = len(res.keys)
    print(f"  提取键合计: {total_keys}"
          + (f"（与原版相同跳过 {res.vanilla_covered}）" if res.vanilla_covered else ""))
    if res.no_source_name_keys:
        print(f"  无原文占位键（值为标识符，请改译）: {len(res.no_source_name_keys)}")
    suggested = [s for s in res.suggested_keys
                 if s.split("（")[0].strip().lower() not in res.keys]
    if suggested:
        print(f"  建议手动补写的键: {len(suggested)} "
              f"(如 {', '.join(suggested[:3])})")
    if res.untranslatable:
        sample = " | ".join(res.untranslatable[:3])
        print(f"  ⚠ 多词事件原文 {len(res.untranslatable)} 条无法经汉化包覆盖（样本: {sample}）")
        print(f"    → 可用 --mode inplace --rewrite-events 标签化改写后翻译")
    for c in res.inline_caveats:
        print(f"  ⚠ {c}")
    for n in res.notes:
        print(f"  注: {n}")
    for w in (fl.warnings if fl else []):
        print(f"  警告: {w}")
    for o in job.output_desc:
        print(f"  输出: {o}")


# ============================================================================
# 哈希校验模式（--verify-hash）
# ============================================================================

def verify_hashes(mod_dirs: List[Path]) -> None:
    total = with_hash = matched = mismatched = 0
    old_mismatched = 0
    failures: List[str] = []
    # 跨模组解析表：名称与 workshopid → 目录
    by_name: Dict[str, Path] = {}
    by_id: Dict[str, Path] = {}
    parsed: List[FileList] = []
    for md in mod_dirs:
        fl0 = parse_filelist(md)
        if not fl0.error:
            parsed.append(fl0)
            if fl0.name:
                by_name.setdefault(fl0.name.strip().lower(), md)
            if fl0.steamworkshopid:
                by_id.setdefault(fl0.steamworkshopid.strip().lower(), md)

    def lookup(name: str) -> Optional[Path]:
        low = name.strip().lower()
        return by_id.get(low) or by_name.get(low)

    def ver_tuple(v: str):
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return None

    for fl in parsed:
        total += 1
        if not fl.expectedhash:
            continue
        with_hash += 1
        # 带跨模组解析重新解析一次
        fl2 = parse_filelist(fl.mod_root, lookup)
        entries = [(e.ctype, e.path) for e in fl2.entries]
        calc = package_hash(entries, fl2.name, fl2.modversion)
        if calc.casefold() == fl.expectedhash.casefold():
            matched += 1
        else:
            mismatched += 1
            vt = ver_tuple(fl.gameversion)
            tag = ""
            if vt is not None and vt < (1, 1, 0, 0):
                old_mismatched += 1
                tag = " [旧版模组：游戏本就不校验其哈希]"
            failures.append(f"{fl.mod_root.name}: 计算 {calc} ≠ 存储 {fl.expectedhash} ({fl.name}){tag}")
    print(f"模组总数 {total}，带 expectedhash {with_hash}，匹配 {matched}，不匹配 {mismatched}"
          + (f"（其中旧版算法 {old_mismatched}，游戏不校验）" if old_mismatched else ""))
    for f in failures[:25]:
        print("  " + f)
    if mismatched > 25:
        print(f"  …另有 {mismatched - 25} 个未列出")


# ============================================================================
# 主流程
# ============================================================================

def normalize_lang(lang: str) -> Tuple[str, str, str, str]:
    """→ (lang_key小写, 规范名, nowhitespace, translatedname)。"""
    key = lang.strip().lower()
    if key in LANG_ALIASES:
        key = LANG_ALIASES[key]
    if key in LANGUAGES:
        canonical, nws, tname = LANGUAGES[key]
        return key, canonical, nws, tname
    # 未知语言：按是否 CJK 猜 nowhitespace
    return key, lang.strip(), "false", lang.strip()


def detect_localmods(game_dir: Optional[Path]) -> Optional[Path]:
    if game_dir is not None:
        lm = game_dir / "LocalMods"
        if lm.is_dir():
            return lm
    # 输入路径位于 LocalMods 内时，用同级
    return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Barotrauma 模组本地化提取工具 v" + VERSION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python Barotrauma_locale_extract.py <模组目录>
  python Barotrauma_locale_extract.py <WorkshopMods/Installed 目录>          # 批量
  python Barotrauma_locale_extract.py <模组目录> --mode inplace
  python Barotrauma_locale_extract.py <模组目录> --mode inplace --rewrite-events
  python Barotrauma_locale_extract.py <目录> --verify-hash                  # 仅校验哈希算法
  python Barotrauma_locale_extract.py <目录> --lang "简体中文,繁体中文"      # 多语言
""")
    ap.add_argument("input", nargs="?", help="模组目录（含 filelist.xml）或包含多个模组的目录")
    ap.add_argument("--mode", choices=["separate", "inplace"], default="separate",
                    help="输出模式：separate=独立汉化包（默认），inplace=就地修改原模组")
    ap.add_argument("--out", default=None, help="separate 模式输出根目录（默认自动探测 LocalMods，失败用 ./汉化包输出）")
    ap.add_argument("--lang", default=DEFAULT_TARGET_LANG,
                    help=f"目标语言，可逗号分隔多个（默认 {DEFAULT_TARGET_LANG}；支持中文别名）")
    ap.add_argument("--force", action="store_true", help="已有目标语言仍强制提取（输出用 <override> 包裹确保覆盖）")
    ap.add_argument("--single-package", action="store_true",
                    help="批量时合并为一个汉化包模组（Text_<模组名>/ 结构）")
    ap.add_argument("--package-name", default="模组汉化补丁整合", help="--single-package 时的汉化包名称")
    ap.add_argument("--modname-suffix", default="汉化包", help="separate 模式生成的模组名后缀")
    ap.add_argument("--rewrite-events", action="store_true",
                    help="inplace 模式：将事件多词原文标签化改写（官方 dumpeventtexts 方式）")
    ap.add_argument("--no-fix-inline", dest="fix_inline", action="store_false",
                    help="inplace 模式：不自动修复升级模块/阵营的内联名称")
    ap.add_argument("--no-events", action="store_true", help="跳过事件文本处理")
    ap.add_argument("--no-conversations", action="store_true", help="跳过 NPC 对话文件处理")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写任何文件")
    ap.add_argument("--game-dir", default=None, help="Barotrauma 游戏目录（用于原版键比对与 LocalMods 探测）")
    ap.add_argument("--verify-hash", action="store_true", help="仅复算输入目录下所有模组的 expectedhash 并报告")
    ap.add_argument("--report", default=None, help="批量汇总 CSV 输出路径")
    args = ap.parse_args(argv)

    if args.verify_hash:
        if not args.input:
            ap.error("--verify-hash 需要一个模组目录参数")
        mods, _ = find_mod_dirs(Path(args.input))
        if not mods:
            print("未找到任何模组（目录中无 filelist.xml）")
            return 1
        verify_hashes(mods)
        return 0

    if not args.input:
        ap.error("需要输入路径：模组目录或包含多个模组的目录")
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"输入路径不存在: {input_path}")
        return 1

    mod_dirs, is_batch = find_mod_dirs(input_path)
    if not mod_dirs:
        print(f"未在 {input_path} 找到任何模组（需含 filelist.xml）")
        return 1

    # 目标语言（多语言时逐一生成；已有语言检测对所有请求语言生效）
    lang_specs = [normalize_lang(x) for x in args.lang.split(",") if x.strip()]
    if not lang_specs:
        lang_specs = [normalize_lang(DEFAULT_TARGET_LANG)]
    main_key, main_display, main_nws, main_tname = lang_specs[0]
    all_lang_keys = {spec[0] for spec in lang_specs}

    game_dir = detect_game_dir(args.game_dir)
    vanilla_english: Dict[str, str] = {}
    vanilla_target: Dict[str, str] = {}
    if game_dir is not None:
        vanilla_english = load_vanilla_pack(game_dir, "english")
        vanilla_target = load_vanilla_pack(game_dir, main_key)
        print(f"游戏目录: {game_dir}（原版英文键 {len(vanilla_english)} 个，"
              f"原版 {main_display} 键 {len(vanilla_target)} 个）")
    else:
        print("提示: 未探测到游戏目录，跳过与原版键的比对（可能重复定义原版已有翻译的键）")

    # 跨模组路径解析表（%ModDir:其他模组名或workshopid%）
    mod_by_name: Dict[str, Path] = {}
    mod_by_id: Dict[str, Path] = {}
    for md in mod_dirs:
        fl = parse_filelist(md)
        if not fl.error:
            if fl.name:
                mod_by_name.setdefault(fl.name.strip().lower(), md)
            if fl.steamworkshopid:
                mod_by_id.setdefault(fl.steamworkshopid.strip().lower(), md)

    def cross_mod_lookup(name: str) -> Optional[Path]:
        low = name.strip().lower()
        return mod_by_id.get(low) or mod_by_name.get(low)

    # 输出根目录
    out_root: Optional[Path] = None
    if args.mode == "separate":
        if args.out:
            out_root = Path(args.out)
        else:
            lm = detect_localmods(game_dir)
            out_root = lm if lm is not None else Path("./汉化包输出")
        if not args.dry_run:
            out_root.mkdir(parents=True, exist_ok=True)
        print(f"输出模式: separate → {out_root}" + ("（批量，每模组一个汉化包）" if is_batch else ""))
    else:
        print("输出模式: inplace（就地修改原模组并重算 expectedhash）")
    if args.dry_run:
        print("!!! dry-run：不写入任何文件")
    if len(lang_specs) > 1:
        print("目标语言: " + ", ".join(spec[1] for spec in lang_specs))

    class _Opts:
        pass

    opts = _Opts()
    opts.target_lang_key = main_key
    opts.target_lang_keys = all_lang_keys
    opts.target_lang_display = main_display
    opts.target_nowhitespace = main_nws
    opts.target_translatedname = main_tname
    opts.vanilla_target_keys = vanilla_target if vanilla_target else None
    opts.force = args.force
    opts.no_events = args.no_events
    opts.no_conversations = args.no_conversations
    opts.dry_run = args.dry_run
    opts.rewrite_events = args.rewrite_events
    opts.fix_inline = args.fix_inline
    opts.modname_suffix = args.modname_suffix

    def log(msg: str):
        print(msg)

    opts.log = log

    single_pkg_root: Optional[Path] = None
    shared_entries: List[Tuple[str, str]] = []
    shared_gameversion: List[str] = []
    if args.single_package and args.mode == "separate" and is_batch:
        single_pkg_root = out_root / sanitize_filename(args.package_name)
        if not args.dry_run:
            single_pkg_root.mkdir(parents=True, exist_ok=True)
        print(f"批量合并汉化包: {single_pkg_root}")

    rows = []
    extracted = skipped = failed = 0
    for idx, md in enumerate(mod_dirs, 1):
        job = ModJob(md, opts)
        try:
            analyze_mod(job, cross_mod_lookup, vanilla_english)
        except Exception as ex:  # 单模组失败不中断批量
            failed += 1
            print(f"[{idx}/{len(mod_dirs)}] {md.name} 处理异常: {ex!r}")
            continue
        if job.skip_reason:
            skipped += 1
        elif not job.res.keys and not job.res.conversations:
            job.skip_reason = "未提取到任何可译文本"
            skipped += 1
        else:
            extracted += 1
            # 逐语言写出（提取结果与语言无关）
            for spec in lang_specs:
                opts.target_lang_key = spec[0]
                opts.target_lang_display = spec[1]
                opts.target_nowhitespace = spec[2]
                opts.target_translatedname = spec[3]
                if args.mode == "separate":
                    if single_pkg_root is not None:
                        pkg = single_pkg_root / sanitize_filename(f"Text_{job.fl.name}")
                        apply_separate(job, single_pkg_root, pkg_root=pkg,
                                       name_prefix=job.fl.name,
                                       shared_entries=shared_entries,
                                       shared_gameversion=shared_gameversion)
                    else:
                        apply_separate(job, out_root)
                else:
                    apply_inplace(job)
            # 恢复主语言（报告显示用）
            opts.target_lang_key, opts.target_lang_display = main_key, main_display
        print_mod_report(job, idx, len(mod_dirs))
        rows.append({
            "模组": job.fl.name if job.fl else md.name,
            "目录": str(md),
            "workshopid": job.fl.steamworkshopid if job.fl else "",
            "状态": "跳过:" + (job.skip_reason or "") if job.skip_reason else "已提取",
            "已有语言": ";".join(job.existing_langs),
            "提取键数": len(job.res.keys),
            "无原文占位": len(job.res.no_source_name_keys),
            "建议补写": len(job.res.suggested_keys),
            "不可覆盖文本": len(job.res.untranslatable),
        })

    print()
    print(f"完成: 共 {len(mod_dirs)} 个模组，提取 {extracted}，跳过 {skipped}，异常 {failed}")

    # --single-package：批量结束后写统一的汉化包 filelist
    if single_pkg_root is not None and shared_entries and not args.dry_run:
        gv = shared_gameversion[0] if shared_gameversion else ""
        fl_path = single_pkg_root / "filelist.xml"
        if fl_path.is_file():
            raw = read_text_robust(fl_path)
            for ename, rel in shared_entries:
                pat = re.compile(r"<" + ename + r"\b[^>]*file\s*=\s*[\"']%ModDir%/" + re.escape(rel) + r"[\"']", re.IGNORECASE)
                if not pat.search(raw):
                    raw, _ok = _insert_entry_into_filelist(raw, ename, rel)
            with open(str(fl_path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(raw)
        else:
            text = make_filelist_text(args.package_name, gv, shared_entries)
            with open(str(fl_path), "w", encoding="utf-8-sig", newline="") as f:
                f.write(text)
        print(f"合并汉化包 filelist: {fl_path}（{len(shared_entries)} 个条目）")

    if args.report:
        import csv as _csv
        with open(args.report, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["模组"])
            w.writeheader()
            w.writerows(rows)
        print(f"汇总报告: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
