# Barotrauma 模组本地化提取工具（Barotrauma_locale_extract.py）

对任意 Barotrauma 模组（重点是未本地化的创意工坊模组）自动提取全部可译文本，
按游戏官方 `infotexts` 格式生成目标语言（默认简体中文）本地化文件，**值填英文原文**，
译者直接覆写即可完成翻译。

- 单文件、零依赖（仅需 Python 3.8+ 标准库）
- 全部提取规则对照游戏源码（FakeFishGames/Barotrauma）逐条核实
- 已在本机 815 个已装工坊模组上完成回验：哈希算法 598/598 可验证样本全部吻合、
  全量提取零异常；与官方自带简中的模组（Beacons Extended）键集对照 37/37 完全一致

---

## 快速开始

```bash
# 单个模组 → 生成独立汉化包（默认输出到游戏 LocalMods，需能探测到 Steam 库）
python Barotrauma_locale_extract.py "C:\...\WorkshopMods\Installed\2282010683"

# 先看看会提取什么，不写任何文件
python Barotrauma_locale_extract.py <模组路径> --dry-run

# 批量：整个 WorkshopMods\Installed 目录（自动跳过已有简中的模组）
python Barotrauma_locale_extract.py "C:\...\WorkshopMods\Installed" --report 汇总.csv

# 批量并合并成一个汉化包（对齐"汉化补丁整合包"的组织习惯：Text_<模组名>/）
python Barotrauma_locale_extract.py <目录> --single-package --package-name "XX汉化补丁整合"

# 简体+繁体一次生成
python Barotrauma_locale_extract.py <模组路径> --lang "简体中文,繁体中文"

# 输出到指定目录而不是 LocalMods
python Barotrauma_locale_extract.py <模组路径> --out "D:\汉化工作区"
```

生成的汉化包结构：

```
<模组名> 汉化包/
├── filelist.xml                                     ← 已注册 <Text>（及 <NPCConversations>）
└── Localization/
    └── SimplifiedChinese/
        ├── <模组名>SimplifiedChinese.xml            ← 待翻译文本（值为英文原文）
        └── <对话文件名>_SimplifiedChinese.xml        ← NPC 对话副本（如模组带对话）
```

在游戏内启用顺序放在**原模组之后**即可生效。纯文本模组不参与联机内容校验
（`TextFile` 标记为 `NotSyncedInMultiplayer`），联机安全。

---

## 两种输出模式

### `--mode separate`（默认）：独立汉化包

不改原模组、不怕工坊更新、联机安全。适合绝大多数场景，也是社区汉化组的标准做法。

### `--mode inplace`：就地修改原模组

在原模组内新建 `Localization/` 并把 `<Text>` 注册进其 `filelist.xml`。

- **自动重算 expectedhash**：游戏会拒载 filelist 被改过的工坊模组（哈希不符 =
  FatalLoadError）。脚本完整复现了游戏的哈希算法（按注册顺序串联各文件"去空白
  UTF-8 MD5"+ 包名 + modversion），修改前先复算原哈希自检，通过才写入新值；
  首次修改自动备份 `filelist.xml.bak`。
- **`--fix-inline`（默认开启）**：自动修复两类"内联名称无法被文本键覆盖"的问题——
  升级模块补 `nameidentifier`/`descriptionidentifier`、阵营把内联
  name/description 移入文本键（显示不变，但变得可翻译）。
- **`--rewrite-events`**：把事件中的多词原文按官方 `dumpeventtexts` 算法标签化
  改写（`text="Hello there"` → `text="EventText.xxx.c1"` + 文本键），使汉化包
  无法覆盖的事件对话变得可翻译。会改写事件 XML（写回前做 XML 合法性校验）。
- 注意：创意工坊更新会覆盖就地修改；与服务器哈希比对会不一致（单机推荐）。

---

## 命令行参数

| 参数 | 说明 |
|---|---|
| `input` | 模组目录（含 filelist.xml）或包含多个模组的目录（自动批量） |
| `--mode separate\|inplace` | 输出模式，默认 separate |
| `--out <目录>` | separate 模式输出根目录（默认自动探测游戏 LocalMods，失败用 `./汉化包输出`） |
| `--lang <语言>` | 目标语言，可逗号分隔多个。默认 `Simplified Chinese`；支持别名：简体中文/繁体中文/schinese/tchinese 等 |
| `--force` | 已有目标语言仍强制提取（separate 模式输出用 `<override>` 包裹，确保覆盖原译） |
| `--single-package` | 批量时合并为一个汉化包（`Text_<模组名>/` 结构） |
| `--package-name <名>` | 合并包名称（默认"模组汉化补丁整合"） |
| `--modname-suffix <后缀>` | 每模组汉化包名后缀（默认"汉化包"） |
| `--rewrite-events` | inplace：事件原文标签化改写（官方方式） |
| `--no-fix-inline` | inplace：不自动修复升级/阵营内联名称 |
| `--no-events` / `--no-conversations` | 跳过事件文本 / NPC 对话处理 |
| `--dry-run` | 只报告，不写任何文件 |
| `--game-dir <目录>` | 手动指定游戏目录（用于原版键比对与 LocalMods 探测；默认自动扫 Steam 库） |
| `--verify-hash` | 仅复算输入目录下所有模组的 expectedhash 并报告（算法自检用） |
| `--report <路径>` | 批量汇总 CSV 输出 |

---

## 提取范围（键规则均已在源码核实）

| 内容类型 | 文本键 | 值 |
|---|---|---|
| 物品 / 结构 / 物品组合 | `entityname.<nameidentifier或identifier>`、`entitydescription.<descriptionidentifier或nameidentifier或identifier>` | 内联 name/description（键优先于内联，汉化包可直接覆盖） |
| 角色 | `character.<speciestranslationoverride或speciesname>` | 无原文，值为物种名占位 |
| 疾病 | `afflictionname/description/causeofdeath/causeofdeathself.<translationoverride或identifier>` | 内联属性（无原文的键列入报告建议） |
| 职业 | `jobname.<identifier>`（描述键列入建议） | 占位 |
| 任务 | `missionname/missiondescription.<textidentifier或identifier>`；header/message/success/failure/sonarlabel 列入建议 | 内联 name/description |
| 升级模块/分类 | `upgradename/upgradedescription/upgradecategory.<…>` | 内联（⚠ 内联优先，inplace 模式自动修复） |
| 天赋 | `talentname.<identifier>`（或 nameidentifier 原始键） | 占位 |
| 阵营 | `faction.<id>[.description/.shortdescription]` | 内联（⚠ 同上） |
| 物品容器标签 | `tagname/tagdescription.<…>` | 占位 |
| 事件 | `eventname.<id>`；已标签化文本自动转出；无空格原文以其本身为键 | 原文 |
| 模组自带文本文件 | 全部键整包转出（最完整的来源） | 原值 |
| NPC 对话 | 整文件生成目标语言副本（仅改根元素 language/nowhitespace，其余原样保留） | 原文待译 |

**安全机制**：与原版英文键值完全相同（或无原文但原版已有该键）的键一律不输出——
否则会在同一语言下与原版译文形成"随机二选一"。事件单词原文也会对照原版键表
避免误覆盖（如 `<loading>` 这类原版 UI 键）。

**重跑安全**：目标文件已存在时只追加缺失键（带 `<!-- 以下为本轮新增键 -->` 注释），
已有翻译不会被覆盖；对话副本已存在则跳过。模组更新后加 `--force` 重跑即可增量补键。

---

## 报告解读

- **无原文占位键**：值为标识符（如 `<jobname.engineer>engineer</jobname.engineer>`），
  是因为模组本身没有任何英文原文（游戏当前显示的也是裸标识符），请直接改为译文。
- **建议手动补写的键**：游戏只从文本键取值、而模组未提供原文的句子类键
  （如 `missionheader0.xxx`、`afflictiondescription.xxx`），按需手动添加。
- **⚠ 多词事件原文**：事件对话原文含空格时无法作为 XML 元素名被汉化包覆盖，
  需 `--mode inplace --rewrite-events` 标签化后翻译。
- **⚠ 内联名称**：升级模块/阵营/角色 DisplayName 的内联值优先于文本键（游戏机制），
  separate 模式无法覆盖，inplace 模式自动修复（角色 DisplayName 除外，需手改模组）。

## 已知限制

- `.sub` 潜艇文件（二进制）内的房间名等不可提取
- 依赖其他模组定义的内容（`%ModDir:其他模组%` 引用、跨模组物种）不在本模组提取范围
- 模组作者自带的"死键"文本文件（在目录里但未在 filelist 注册）不会被游戏加载，
  本工具以注册情况为准
- inplace 模式的哈希处理：若模组文件在安装后被外部改动过（复算与存储值不符），
  脚本会移除 expectedhash 属性保证可加载（游戏对空值不校验），并在报告中说明

## 原理速览

- **文本包格式**：`<infotexts language="Simplified Chinese" nowhitespace="true"
  translatedname="中文（简体）">`，子元素名即键、内文即值；`<override>` 包裹可
  确定性覆盖同语言他包的重复键（否则随机二选一）
- **名称解析优先级**（物品为例）：`entityname.<nameidentifier>` →
  `entityname.<identifier>` → `entityname.<fallbacknameidentifier>` → 内联 `name`
- **包哈希**：MD5 串联（各注册文件哈希 → 包名 → modversion）；
  单文件哈希 = MD5(去 Unicode 空白的 UTF-8 文本)；`Other/UIStyle/Sounds/Particles`
  等类型固定为 16 零字节；`.sub` 等按原始字节；`Submarine` 等类型按字节 MD5。
  gameversion < 1.1.0.0 的模组游戏不校验哈希
