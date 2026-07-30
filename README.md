# 天巡 PX6C / PX4 中文无人机地面站

## 项目介绍

本项目是在现有深色工程风格无人机 UI 原型上持续扩展的本地 Web 地面站。界面保留任务总览、遥测卡片、GPS 地图、告警、姿态仪、趋势图和机载状态，并增加 PX6C/Pixhawk 6C、PX4、MAVLink、实时日志、安全模式、调参与任务规划功能。

## 当前功能

- PX6C / Pixhawk 6C USB 串口连接。
- PX4 MAVLink UDP 监听，默认端口 `14550`。
- 演示模式和 MAVLink 模拟飞行。
- HEARTBEAT、ATTITUDE、GPS、位置、速度、电池、RC、舵机、Home 点、STATUSTEXT、参数和任务消息解析。
- 真实 GPS 地图、Home 点和实际飞行轨迹。
- 人工地平仪及俯仰、横滚、航向显示。
- 统一飞行状态模型。
- CSV 与 SQLite 实时遥测日志。
- 实机只读模式和实机指令模式。
- 调参与测试安全框架。
- PID 待写入队列。
- 地图点击添加、拖动和删除航点。
- 任务上传前安全检查。

## 安装

正常情况下不需要手动安装依赖。直接运行 `start-ui.cmd` 时，系统会自动完成：

- 检查 Python 运行环境。
- 检查并一次性安装 `requirements.txt` 中的串口、MAVLink、ULog、报告、图表依赖。
- 清理旧的 UI / 后端 / 连接进程。
- 启动地面站后端并自动打开浏览器。

如果只想单独检查依赖，可运行：

```powershell
.\ensure-dependencies.ps1
```

## 运行

推荐只使用这一条命令启动地面站：

```powershell
.\start-ui.cmd
```

不要再单独启动 `ground_station_server.py`、`px6c_connector.py`、`mavlink_bridge.py`、`run-ai-report-server.cmd` 或其它旧脚本；这些能力统一由主后端和 UI 页面调度，避免端口占用、串口争抢和报告依赖缺失。

浏览器地址：

```text
http://127.0.0.1:8080
```

## 演示模式

进入“连接设置”，选择“演示模式”，点击“开始连接”。页面必须显示“演示模式”，演示位置不代表真实 GPS。

## PX6C / PX4 USB 连接

1. 使用数据线或数传电台连接飞控。
2. 打开“连接设置”。
3. 选择“USB 串口连接”。
4. 选择飞控对应的 `COM` 端口。
5. 波特率通常选择 `57600` 或 `115200`。
6. 点击“开始连接”。

真实飞控连接后默认进入“实机只读模式”。

不要再单独运行 `px6c_connector.py`。所有连接均从 UI 的“连接设置”页面启动，否则多个进程会争抢同一个串口。

## WiFi UDP 连接

1. 让 PX4 或数传设备向地面站电脑发送 MAVLink UDP。
2. 默认监听地址为 `0.0.0.0`。
3. 默认监听端口为 `14550`。
4. 在“连接设置”选择“WiFi UDP 连接”并开始连接。

3 秒没有数据时提示“数传连接可能丢失”，5 秒没有数据时提示“WiFi 数传连接丢失”。

## 实时日志

在“连接设置”页面输入会话名称，然后点击“开始记录”。日志保存到：

```text
logs/日期_时间_flight/
```

每个会话包含：

- `telemetry.csv`
- `events.csv`
- `warnings.csv`
- `parameters.csv`
- `mission.json`
- `session.db`

## 历史日志

左侧点击“飞行记录”查看本地会话。会话摘要包含数据条数、最大高度、最大速度、最低电压和报警数量。日志文件可直接使用 Excel、SQLite 工具或后续回放模块打开。

## 调参与测试

“调参与测试”页面包含：

- 舵机 PWM 显示与安全启用流程。
- 电机测试拆桨确认和 10% UI 输出上限。
- 紧急停止。
- PX4 常用 Roll/Pitch/Yaw/Altitude PID 参数。
- 参数待写入队列和写入前确认。

当前危险指令发送保持关闭，只完成安全判断、确认和队列框架。

## 任务规划

进入“任务规划”后：

1. 点击地图添加航点。
2. 拖动标记调整位置。
3. 在表格中设置指令、高度、停留时间和速度。
4. 保存或导出 JSON。
5. 执行“上传前检查”。

检查内容包括连接状态、运行模式、GPS、Home 点、电池、航点和降落/返航指令。

## 安全警告

**请勿在安装螺旋桨时进行电机测试。**

**首次连接真实无人机时，请使用实机只读模式。**

**任何解锁、电机测试、任务上传操作都必须在安全环境下进行。**

## 常见问题

### UI 显示在线但地图没有飞机

飞控尚未获得有效 `3D Fix`。请将 GPS 天线移动到室外开阔区域。GPS 无效时系统不会显示虚假真实位置。

### 串口连接失败

关闭 QGroundControl 等可能占用同一串口的软件，然后刷新串口列表。

### 地图没有瓦片

检查网络连接。瓦片无法加载时，GPS 遥测和日志仍会继续更新。

### 参数或任务无法上传

确认已切换到“实机指令模式”，并查看安全管理器返回的中文原因。所有危险操作仍需单独确认。

## AI PID Advisor 与飞行报告工程链路

本项目的 AI 调参不是直接把原始 `.ulg` 日志交给模型生成结论，而是按工程闭环处理：

1. `services/feature_extractor.py` 从实时遥测或 ULG 日志中提取姿态误差、振荡、执行器饱和、电池、GPS、振动和缺失数据。
2. `services/ai_pid_advisor.py` 使用本地工程规则生成 `pid-advisor.v1` 结构化建议，输出参数名、当前值、建议值、变化百分比、依据、风险和缺失数据。
3. `config/pid_parameter_whitelist.json` 限制允许调整的 PX4 PID 参数、绝对范围和单次调整幅度。
4. `services/safety_gate.py` 在写入前检查未解锁、确认文本、白名单、参数范围和单次调整比例。
5. `services/rollback_manager.py` 在写入前保存回滚快照，后续可查看历史并回滚。
6. `/api/ai/pid/apply` 通过现有 PX6C MAVLink 命令队列写入参数，不绕过原有安全机制。

飞行报告生成继续由 `services/ulg_analyzer.py` 完成 Word、PDF、Markdown、HTML 和图表输出；新增 `services/report_data_builder.py` 生成结构化底稿，包含机型识别、复合翼/固定翼阶段姿态分析、缺失字段、告警事件和 PID 输入特征，便于复核报告依据。

### AI 相关接口

- `GET /api/ai/status`：查看 Advisor 模式、白名单数量和 Mock 示例。
- `POST /api/ai/pid/analyze`：使用实时遥测、Mock 或上传日志生成结构化 PID 建议。
- `POST /api/ai/pid/apply`：通过安全门后将建议加入 MAVLink 参数写入队列。
- `GET /api/ai/pid/history`：查看 PID 写入和回滚快照。
- `POST /api/ai/pid/rollback`：通过安全门后将快照中的旧参数加入回滚队列。

### 接入 ChatGPT / OpenAI 辅助调参

默认情况下，AI PID Advisor 使用本地工程规则，不会把原始 `.ulg` 日志上传到外部平台。

如需让 ChatGPT / OpenAI 参与分析，请在项目根目录新建 `.env` 文件：

```text
AI_PROVIDER=openai
AI_MODEL=gpt-5.5
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=你的 OpenAI API Key
```

UI 的“调参与测试 → AI PID 调参助手”里可以选择：

- `本地工程规则`：只在本机根据日志特征和工程规则生成建议。
- `ChatGPT / OpenAI`：后端先解析日志，只把结构化特征 JSON 发给模型，不直接发送原始日志文件。

无论使用哪个 AI 引擎，输出都必须继续经过：

- PID 参数白名单；
- 参数范围和单次调整幅度限制；
- 实机安全状态检查；
- 人工确认文本；
- 回滚快照；
- PX6C MAVLink 命令队列。

如果 ChatGPT / OpenAI 调用失败，系统会回退到本地工程规则 Advisor，并在结果风险提示中说明原因。

### 安全原则

- AI 只给建议，不直接控制飞行。
- 默认不上传原始日志；选择 ChatGPT/OpenAI 时也只上传结构化特征 JSON，不直接上传原始 `.ulg` 文件。

### AI 分级模型、成本统计与案例库

当前系统不进行模型 fine-tuning。历史飞行数据仅用于本地案例检索和辅助分析，不会通过普通 API 调用训练模型。

AI 调用分为三档：

- `fast_check`：快速初筛，默认 `AI_MODEL_FAST`，适合日常预览和低成本检查。
- `standard_analysis`：标准分析，默认 `AI_MODEL_DEFAULT`，适合 AI 报告和 AI PID Advisor 的日常使用。
- `final_review`：最终复核，默认 `AI_MODEL_FINAL`，只建议用于正式报告、高风险复盘和重要 PID 建议复核。

推荐 `.env` 配置：

```env
AI_PROVIDER=openai
AI_MODEL_FAST=gpt-5.4-nano
AI_MODEL_DEFAULT=gpt-5.4-mini
AI_MODEL_FINAL=gpt-5.5
AI_REPORT_MODEL=gpt-5.4-mini
AI_PID_MODEL=gpt-5.4-mini
AI_FINAL_REVIEW_MODEL=gpt-5.5
OPENAI_API_KEY=你的 OpenAI API Key
```

新增模块：

- `services/ai_model_config.py`：读取多模型分级配置。
- `services/token_usage.py`：记录每次 AI 调用 tokens 和估算成本。
- `services/flight_case_library.py`：保存 AI 报告和 AI PID 分析案例。
- `services/similar_case_retriever.py`：用规则法检索相似历史案例。
- `config/model_pricing.json`：可修改的模型价格表。

安全边界保持不变：AI 只输出分析和建议，不直接控制飞控，不发送 MAVLink command，不自动 Arm/Disarm/RTL/Land，不在飞行中自动修改 PID。参数写入仍必须经过本地安全门、Disarmed 状态、人工确认、备份和回滚机制。
- 参数写入必须未解锁，并输入确认文本：`确认写入参数`。
- 单次 PID 调整默认限制在白名单定义范围内，建议先小幅试飞再扩大。

## 前端缓存与版本检查

当前项目不是 React/Vite/Vue/Next 打包项目，而是原生 `index.html`、`app.js`、`styles.css`，由 `ground_station_server.py` 直接托管。为避免浏览器加载旧 UI，系统已增加以下机制：

- `GET /api/version` 和 `/version.json` 返回当前前端 hash、后端启动时间、Git commit（如果可用）和核心文件 hash。
- `index.html` 返回 `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`，浏览器每次都会重新确认入口文件。
- `/api/` 接口统一返回 `Cache-Control: no-store`，避免遥测、AI 状态、报告状态等接口拿到旧数据。
- 通过 `?v=<frontendHash>` 给 `app.js` 和 `styles.css` 自动加版本号；带版本号的静态资源可使用长期缓存。
- 开发环境访问 `localhost / 127.0.0.1` 时，前端会自动 unregister 旧 Service Worker，并清理常见 PWA/Workbox 缓存。
- 页面顶部会显示 `Build xxxxxxxx`，鼠标悬停可查看版本、hash、后端启动时间和 commit。
- 前端每 30 秒检查一次 `/api/version`，如果检测到版本变化，会提示刷新页面。

手动清理缓存：

1. 停止旧后台进程：
   ```powershell
   .\cleanup-ui.ps1
   ```
2. 删除浏览器缓存：Chrome 按 `Ctrl + Shift + Delete`，选择“缓存的图片和文件”，清理后重新打开 `http://127.0.0.1:8080`。
3. 清理 Service Worker：Chrome 打开 DevTools -> Application -> Service Workers，点击 unregister。
4. 清理 Cache Storage：DevTools -> Application -> Storage -> Clear site data。
5. 如果以后改成 Vite/webpack，再删除 `.vite`、`node_modules/.cache`、`dist` 后重新 build；当前项目默认没有这些缓存目录。
6. 强制刷新页面：在地址后加时间戳，例如 `http://127.0.0.1:8080/?v=20260701`，或按 `Ctrl + F5`。

验证当前是否为最新版本：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/version
```

确认返回的 `frontendHash` 与页面顶部 `Build` 前 8 位一致；如果不一致，按上面的步骤清缓存并重启 UI。
