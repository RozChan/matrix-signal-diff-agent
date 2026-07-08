# matrix-signal-diff-agent

`matrix-signal-diff-agent` 是一个本地 Streamlit Demo，用于基于已有 legacy Python 脚本识别 EEA 4.0 / 5.1 矩阵中“同一信号”的定义差异。

本阶段只做本地网页 Demo：不接飞书、不接真实大模型 API、不使用数据库，也不做正式服务化部署。

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
- 未接入真实大模型 API；
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
