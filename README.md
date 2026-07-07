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

## 安装依赖

建议使用 Python 虚拟环境：

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

脚本内容为：

```bat
python -m streamlit run app.py
```

### 命令行启动

```bash
python -m streamlit run app.py
```

启动后浏览器会打开本地 Streamlit 页面。

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
