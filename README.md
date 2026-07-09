# matrix-signal-diff-agent

`matrix-signal-diff-agent` 是一个本地 Streamlit Demo，用于基于已有 legacy Python 脚本识别 EEA 4.0 / 5.1 矩阵中“同一信号”的定义差异。

本阶段只做本地网页 Demo：不接飞书、不使用数据库，也不做正式服务化部署；AI 辅助复核默认关闭，只有用户自行配置 OpenAI-compatible 接口并勾选后才会调用模型。

## 项目用途

在架构升级场景中，4.0 与 5.1 矩阵里可能存在同名信号，但信号长度、精度、偏移量、物理最小值、物理最大值、单位或信号值描述发生变化。本工具会复用仓库中的 4 个 legacy 脚本，自动完成：

1. 提取 4.0 / 5.1 全量信号矩阵清单；
2. 按信号名称生成同名去重结果；
3. 生成最终同一信号差异识别 Excel。

## 工程结构

```text
matrix-signal-diff-agent/
├─ app.py
├─ requirements.txt
├─ start_demo.bat
├─ README.md
├─ common_matrix_utils_local_v13.py
├─ 01_extract_full_matrix_local_v13.py
├─ 02_generate_dedup_signals_local_v13.py
├─ 03_generate_compare_file5_local_v13.py
├─ core/
│  ├─ __init__.py
│  └─ pipeline.py
└─ temp/
```

## 首次运行步骤（Windows 双击方式）

1. 双击 `install_dependencies.bat` 安装依赖；
2. 双击 `start_demo.bat` 启动工具；
3. 浏览器打开后上传 4.0 和 5.1 Excel 文件；
4. 点击“开始识别”。

`start_demo.bat` 会在启动前检查 Streamlit 是否已安装。如果未检测到 Streamlit，会提示先运行 `install_dependencies.bat`，避免直接出现 `No module named streamlit` 报错。

## 安装依赖

### Windows 双击安装

双击：

```text
install_dependencies.bat
```

该脚本会自动切换到项目目录，优先执行：

```bat
python -m pip install -r requirements.txt
```

如果默认源安装失败，脚本会尝试使用清华镜像源：

```bat
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果 pip 不可用，脚本会先尝试：

```bat
python -m ensurepip --upgrade
```

### 命令行安装（可选）

也可以手动使用 Python 虚拟环境：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

`requirements.txt` 至少包含：

- `streamlit`
- `openpyxl`
- `pandas`

## 启动方式

### Windows 双击启动

双击：

```text
start_demo.bat
```

`start_demo.bat` 会先执行 `python -c "import streamlit"` 检查依赖；检查通过后再执行：

```bat
python -m streamlit run app.py
```

### 命令行启动

```bash
python -m streamlit run app.py
```

启动后浏览器会打开本地 Streamlit 页面。

### Windows 双击闪退排查

如果双击 `install_dependencies.bat` 或 `start_demo.bat` 后窗口一闪而过，通常不是业务脚本问题，常见原因包括：

- 当前 Windows 没有把 `python` 加入 `PATH`，批处理找不到 Python；
- 当前 Python 环境没有安装 `pip` 或 `streamlit`；
- 公司网络、代理或 pip 源导致依赖安装失败；
- 批处理没有在项目目录中运行，找不到 `requirements.txt` 或 `app.py`。

两个 bat 脚本都会自动切换到脚本所在目录，并在当前目录生成日志文件：

```text
install_dependencies.log
start_demo.log
```

如果窗口仍然闪退，可以打开 `cmd`，手动进入项目目录后运行：

```bat
install_dependencies.bat
start_demo.bat
```

然后把对应 `.log` 文件中的报错信息复制出来排查。

## 使用流程

1. 在页面上传多个 EEA 4.0 矩阵 Excel 文件，支持 `.xlsx` / `.xlsm`；
2. 在页面上传多个 EEA 5.1 矩阵 Excel 文件，支持 `.xlsx` / `.xlsm`；
3. 点击“开始识别”；
4. 系统会自动创建临时任务目录：

```text
temp/<task_id>/input/4.0
temp/<task_id>/input/5.1
temp/<task_id>/output
```

5. 系统按顺序运行 01、02、03 三个 legacy 脚本；
6. 页面展示进度、统计结果、单文件下载按钮和全部结果 ZIP 下载按钮。

## 输出文件

输出文件名保持 legacy 规则不变：

```text
26R1 4.0全量信号矩阵清单.xlsx
26R2 5.1全量信号矩阵清单.xlsx
26R1 4.0全量信号-同名去重后.xlsx
26R2 5.1全量信号-同名去重后.xlsx
4.0和5.1同一信号差异点识别.xlsx
```

页面可单独下载上述 5 个结果文件，也可以下载全部结果 ZIP。

## 输出文件在哪里

每次运行都会生成独立任务目录，输出位于：

```text
temp/<task_id>/output/
```

其中 `<task_id>` 是系统自动生成的 UUID。页面成功提示中也会展示本次任务目录路径。

## 统计结果

处理完成后，页面会展示：

- 4.0 全量信号数量；
- 5.1 全量信号数量；
- 4.0 去重后信号数量；
- 5.1 去重后信号数量；
- 完全同名匹配信号数；
- sheet1 差异行数；
- sheet2 vcu-hcu 差异行数。

统计从生成的 Excel 文件和 compare 阶段日志读取，不写死业务结果。

## 错误查看

如果任一步失败，页面会显示错误信息，并提供“错误详情（可复制）”。错误详情包含失败脚本名、返回码、stdout 和 stderr，便于定位 Excel 格式、依赖或 legacy 脚本执行问题。

也可以查看临时任务目录下的 legacy 日志：

```text
temp/<task_id>/output/01_extract_full_matrix_local_v13_log.txt
temp/<task_id>/output/02_generate_dedup_signals_local_v13_log.txt
temp/<task_id>/output/03_generate_compare_file5_local_v13_log.txt
```

## 保留的 legacy 业务规则

本 Demo 不从零重写业务规则。`core/pipeline.py` 会把以下 legacy 脚本复制到每次任务目录，并以任务目录为 `cwd` 顺序运行：

- `common_matrix_utils_local_v13.py`
- `01_extract_full_matrix_local_v13.py`
- `02_generate_dedup_signals_local_v13.py`
- `03_generate_compare_file5_local_v13.py`

因此以下规则继续由 legacy 脚本保证：

- 不收集 `History`、修改记录、说明页等非矩阵 sheet；
- `R/r` 标准化为 `r`；
- `T/t/S/s` 标准化为 `s`；
- 4.0 ECU 收发状态保持首个 ECU 为主、后续括号格式；
- 4.0 发送 ECU 汇总 / 接收 ECU 汇总保持括号格式；
- 5.1 ECU 状态保持逐行输出；
- 按信号名称去重；
- 去重时信号定义字段保留首次出现；
- 去重时信号来源文件和 ECU 状态汇总；
- 文件五只输出匹配成功且 7 个字段存在差异的信号；
- ECU 收发状态只作为追溯字段，不参与差异筛选；
- 信号值描述比较继续支持枚举顺序差异、`0x01` / `0x1` 差异、范围枚举等规则。

## 当前未实现内容

- 未接入飞书；
- 未提供内置大模型账号或默认联网模型；
- 未使用数据库；
- 未提供 FastAPI / 正式后端服务；
- 未内置真实 Excel 样例数据。

## AI 辅助复核与人工审核

当前版本会在最终结果文件 `4.0和5.1同一信号差异点识别.xlsx` 中新增一个 sheet：

```text
AI辅助复核与人工审核明细
```

说明：

1. AI 辅助复核默认关闭。
2. 未开启 AI 时，仍会生成 `AI辅助复核与人工审核明细` sheet。
3. 开启 AI 需要复制 `.env.example` 为 `.env` 并配置：

```text
LLM_ENABLED=true
LLM_API_KEY=<你的 API key>
LLM_BASE_URL=<OpenAI-compatible chat completions base url>
LLM_MODEL=<模型名称>
```

4. AI 只复核 `信号值描述` 和 `单位` 等文本类差异。
5. `信号长度`、`精度`、`偏移量`、`物理最小值`、`物理最大值` 等数值类差异不交给 AI 判断。
6. AI 不会修改、删除、覆盖原始两个差异 sheet，也不会修改原始差异结果。
7. 所有差异最终都需要人工审核，AI 结果仅作为人工参考。
8. 人工审核结果可以在 Excel 的 `人工审核结果` 和 `人工备注` 列中填写。
9. 当前版本不做网页端逐条审核。
10. 当前版本不做审核后最终结果导出页。
11. 当前版本不接飞书、不接 Confluence。

如果 `LLM_ENABLED=false` 或未配置 API key，文本类差异会写入人工审核清单，并标记为 `未启用`，原有规则分析流程不受影响。

### AI 配置排查

如果页面提示“已勾选 AI 辅助复核，但未检测到 `LLM_ENABLED=true`”，请检查：

1. `.env` 是否放在项目根目录，也就是 `app.py` 同级目录；
2. Windows 是否把文件实际保存成了 `.env.txt`，建议开启“显示文件扩展名”确认；
3. `.env` 中是否写成 `LLM_ENABLED=true`，不要写成 `True` 以外的其他值，建议全部小写；
4. 修改 `.env` 后是否重新运行了 `start_demo.bat`；
5. 是否已经重新运行 `install_dependencies.bat` 安装 `python-dotenv`；
6. 当前版本也包含内置 `.env` 读取兜底逻辑，即使 `python-dotenv` 缺失，也会尝试读取项目根目录 `.env`。

注意：不要把真实 `LLM_API_KEY` 发到聊天、截图或提交到 Git。`.gitignore` 已忽略 `.env`，但如果 key 已经泄露，应立即到模型平台吊销并重新生成。

### AI 复核连接测试、超时和数量上限

页面的 `AI 配置状态` 区域会展示：

- `LLM_ENABLED` 当前值；
- `LLM_BASE_URL` 是否已配置；
- `LLM_MODEL` 当前值；
- `LLM_API_KEY` 是否已配置（只显示已配置/未配置，不显示 key）；
- 当前连接状态：未测试 / 连接成功 / 连接失败。

点击 `测试大模型连接` 会发送一个极短的 OpenAI-compatible chat completions 请求，默认 15 秒超时，不会显示 API key。

AI 正式复核调用的默认超时为 30 秒，可通过 `.env` 修改：

```text
LLM_TIMEOUT_SECONDS=30
```

页面提供 `本次最多 AI 复核条数` 输入框，默认 50。该上限只限制实际调用模型的文本类差异数量，失败和超时的请求也计入本次调用上限；所有差异仍会写入 `AI辅助复核与人工审核明细` sheet。超过上限的文本类差异会标记为未调用模型，并建议人工确认。

如果未勾选 `启用 AI 辅助复核` 或 `LLM_ENABLED=false`，系统不会调用模型、不会等待网络，只会快速生成待人工审核明细。

## 网页端人工审核工作台

当前版本在原有规则识别和 AI 辅助复核之后，新增了本地网页端人工审核工作台。整体原则不变：legacy 脚本仍负责基础规则识别，AI 只提供辅助建议，不会自动删除差异；最终结论以人工审核结果为准。

### task_id 与任务目录

每次点击“开始识别”都会生成一个 `task_id`，格式类似：

```text
YYYYMMDD_HHMMSS_a1b2c3
```

任务数据保存在：

```text
temp/<task_id>/
├─ input/
│  ├─ 4.0/
│  └─ 5.1/
├─ output/
│  ├─ 26R1 4.0全量信号矩阵清单.xlsx
│  ├─ 26R2 5.1全量信号矩阵清单.xlsx
│  ├─ 26R1 4.0全量信号-同名去重后.xlsx
│  ├─ 26R2 5.1全量信号-同名去重后.xlsx
│  ├─ 4.0和5.1同一信号差异点识别.xlsx
│  └─ 人工审核后最终差异结果.xlsx
├─ review/
│  ├─ review_items.json
│  ├─ review_state.json
│  └─ review_log.jsonl
└─ task_meta.json
```

`task_meta.json` 记录任务创建时间、更新时间、状态、输入文件数量、输出目录、审核数据路径、最终审核文件路径和失败错误信息。页面恢复历史任务时会读取该文件。

### 审核数据文件

系统会继续保留最终差异 Excel 中的 `AI辅助复核与人工审核明细` sheet，作为交付和追溯材料。同时会基于该 sheet 生成结构化审核数据：

- `review/review_items.json`：网页端审核数据源，每条记录代表一个字段级差异；`item_id` 基于来源 sheet、4.0 信号名、5.1 信号名、差异字段、4.0 内容和 5.1 内容生成，不依赖 Excel 行号。
- `review/review_state.json`：人工审核状态，每次保存审核结果或备注后立即写入本地文件，不只保存在 Streamlit session 中。
- `review/review_log.jsonl`：人工审核操作追溯日志，每次更新追加一行 JSON。

### 中途关闭网页后如何恢复

如果浏览器或 Streamlit 页面中途关闭，不需要重新跑矩阵流程。重新打开页面后：

1. 在左侧“继续历史任务”区域选择最近任务，或手动输入 `task_id`；
2. 点击恢复按钮；
3. 页面会读取 `task_meta.json`、`review_items.json` 和 `review_state.json`；
4. 已审核/未审核进度、人工审核结果和备注会从本地 JSON 文件恢复。

### 人工审核结果枚举

网页端每条差异支持填写以下人工审核结果：

- `确认真实差异`
- `确认可忽略`
- `确认错别字`
- `确认语义一致`
- `存疑待确认`

人工备注为自由文本。人工审核结果和备注每次点击“保存当前审核”或“保存并下一条”后都会立即写入 `review_state.json`。

### 筛选与审核方式

人工审核工作台支持按以下条件筛选：

- 来源Sheet：全部、完全同名匹配对比结果、vcu-hcu 同名匹配；
- 差异字段：信号长度、精度、偏移量、物理最小值、物理最大值、单位、信号值描述、未解析；
- AI判断结果：疑似一致、疑似错别字、疑似语义相近、真实差异、无法判断、不适用、未启用；
- 人工审核状态：未审核、已审核和各人工审核结果。

第一版采用分页卡片式审核，避免在复杂表格中直接编辑导致状态混乱。每张卡片展示 4.0/5.1 内容、AI 判断结果、AI 理由、AI 建议处理方式和原始差异点 list。

页面还提供谨慎版批量操作：可对当前筛选结果中的“未审核”记录批量设置为 `确认可忽略` 或 `存疑待确认`，执行前必须勾选确认。

### 生成最终审核结果

审核完成或阶段性审核后，可以点击“生成最终审核结果”。系统会读取：

- `review/review_items.json`
- `review/review_state.json`

并生成：

```text
output/人工审核后最终差异结果.xlsx
```

该 Excel 包含 7 个 sheet：

1. `最终保留差异`：人工审核结果为 `确认真实差异`；
2. `确认可忽略差异`：人工审核结果为 `确认可忽略`；
3. `确认错别字`：人工审核结果为 `确认错别字`；
4. `确认语义一致`：人工审核结果为 `确认语义一致`；
5. `存疑待确认`：人工审核结果为 `存疑待确认`；
6. `未审核`：人工审核结果为空；
7. `审核明细全量`：包含所有字段级差异和人工审核状态。

即使某个分类没有数据，也会保留对应 sheet 和表头，便于固定交付格式。

### 下载内容

页面下载区提供：

- 5 个基础结果 Excel；
- `4.0和5.1同一信号差异点识别.xlsx`，其中包含原始两个差异 sheet 和 `AI辅助复核与人工审核明细` sheet；
- `人工审核后最终差异结果.xlsx`，如果已经生成；
- 全部结果 zip。

全部结果 zip 会包含 output 下的 Excel 结果，以及 `task_meta.json`、`review_items.json`、`review_state.json`、`review_log.jsonl`（如果存在）。

### 当前仍未实现

当前版本仍然是本地 Streamlit Demo：

- 不接飞书；
- 不接 Confluence；
- 不使用数据库；
- 不做 FastAPI / 正式后端服务；
- 不做多人协同；
- 不做权限控制。

后续如果接入飞书，建议飞书只作为任务入口、状态通知和结果链接推送，不建议在聊天中逐条完成人工审核；逐条审核仍建议在网页工作台中完成。

### 人工审核默认策略与排序

为降低逐条审核工作量，当前版本会在初始化 `review_state.json` 时生成系统默认结论，并在网页端优先展示最需要人工判断的记录：

1. 以下记录会默认保留为 `确认真实差异`，`review_source=system_default`，`reviewed=true`：
   - AI判断结果为 `真实差异`；
   - AI建议处理方式为 `应保留差异`；
   - 差异字段为数值类字段：`信号长度`、`精度`、`偏移量`、`物理最小值`、`物理最大值`。
2. 以下记录不会默认给最终结论，会排在审核列表前面，提示优先人工确认：
   - AI判断结果为 `疑似一致`；
   - AI判断结果为 `疑似错别字`；
   - AI判断结果为 `疑似语义相近`；
   - AI建议处理方式为 `可忽略`。
3. `无法判断`、`未启用`、空 AI 判断、未解析字段或其他无法归类情况，也保持待人工确认。
4. 人工可以修改所有系统默认结论。人工修改后 `review_source=manual`，最终导出以人工修改后的 `manual_review_result` 为准。
5. 如果人工未修改系统默认真实差异，该记录会进入最终导出文件的 `最终保留差异` sheet。
6. 如果人工未处理疑似可删除或不确定记录，该记录会进入最终导出文件的 `未审核` sheet。

`review_state.json` 中每条记录会保留系统默认结论与原因：

```json
{
  "manual_review_result": "确认真实差异",
  "manual_note": "",
  "reviewed": true,
  "review_source": "system_default",
  "default_review_result": "确认真实差异",
  "default_reason": "AI或规则判断为真实差异，系统默认保留；人工可修改",
  "reviewed_at": "...",
  "updated_at": "...",
  "reviewer": ""
}
```

人工审核工作台默认排序为：

1. `需人工优先确认`：疑似一致、疑似错别字、疑似语义相近或建议可忽略，且尚未人工修改；
2. `待人工确认`：无法判断、未启用或没有最终结论；
3. `人工已修改`；
4. `系统默认保留`。

页面筛选区新增：

- 审核来源：全部、需人工优先确认、系统默认保留、人工已修改；
- 人工审核状态：全部、待人工确认、已有结论、人工已修改、系统默认结论，以及各人工审核结果。

最终导出的 `审核明细全量` sheet 会额外包含审核来源、系统默认结论和系统默认原因，便于追溯该结论来自系统默认还是人工修改。

### 信号级 AI 复核与人工审核

当前版本将 AI 复核和人工审核粒度从“字段级”调整为“信号级”：

1. 一个审核项对应一个 `来源Sheet + 4.0信号名 + 5.1信号名`；
2. 一个信号可以包含多个字段差异，例如信号长度、精度、物理最大值会聚合到同一条信号级审核项；
3. AI 对一个信号整体判断一次，不再按字段逐条调用；
4. 如果有 60 个信号差异，AI 复核最多就是 60 次，而不是把 130 个字段差异拆成 130 次；
5. 页面进度显示为“第 n / total 个信号”，`total` 是信号数；如需查看字段规模，页面会额外展示涉及差异字段总数。

信号级 AI 判断结果只保留 4 类：

- `真实差异`
- `疑似可忽略`
- `无法判断`
- `未启用`

信号级判断规则：

- 只要信号存在 `信号长度`、`精度`、`偏移量`、`物理最小值`、`物理最大值` 等数值类定义差异，系统会直接判定为 `真实差异`，建议保留；
- 只有在不存在数值类差异、且差异仅为 `单位` 或 `信号值描述` 等文本类差异时，AI 才会判断是否为 `疑似可忽略`；
- AI 无法可靠判断、接口异常或超过本次 AI 复核信号数上限时，会标记为 `无法判断` 并建议人工确认；
- AI 未启用时，含数值类差异的信号仍默认 `真实差异`，仅文本类差异标记为 `未启用`。

`AI辅助复核与人工审核明细` sheet 现在也是一行一个信号，字段包括：差异字段汇总、差异字段数量、是否包含数值类差异、是否包含文本类差异、原始差异点 list、字段差异明细、信号级 AI 判断结果、差异类型汇总、置信度、信号级 AI 判断理由、信号级 AI 建议处理方式、系统默认结论和人工审核列。

`review_items.json` 也改为信号级结构，每条 item 包含：

- `diff_fields`：该信号涉及的所有差异字段；
- `diff_field_count`：差异字段数量；
- `has_numeric_diff` / `has_text_diff`；
- `field_diffs`：字段级明细数组；
- `signal_ai_judgement`、`difference_type_summary`、`signal_ai_reason`、`signal_ai_suggested_action`。

人工审核工作台中，每张卡片代表一个信号。字段差异明细放在卡片内的 expander 中展示。页面颜色/标签含义：

- 🟠 `需人工优先确认`：信号级 AI 判断为 `疑似可忽略`，排在最前面；
- ⚪ `待人工确认`：AI `无法判断` 或 `未启用`，需要人工确认；
- 🟢 `系统默认保留`：信号级判断为 `真实差异`，系统默认保留，但人工可修改；
- 🔵 `人工已修改`：人工已经覆盖系统默认或待确认结论，最终以人工审核结果为准。

排序逻辑调整为：

1. `疑似可忽略` 且未人工修改的信号；
2. `无法判断` / `未启用` 且无最终结论的信号；
3. 人工已修改的信号；
4. 系统默认保留的真实差异信号。

最终导出的 `人工审核后最终差异结果.xlsx` 也按信号级 item 输出，每个 sheet 一行代表一个信号差异项，并保留字段差异明细。旧任务如果仍是字段级 `review_items.json`，页面会提示“当前任务使用旧版字段级审核数据，建议重新运行任务生成信号级审核数据”，不会强制迁移旧结构。
