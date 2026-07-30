# 壹通无人机地面站

本项目是一个面向 PX4 / Pixhawk / PX6C 兼容飞控的本地 Web 无人机地面站。系统由原生 Web 前端、Python 后端、MAVLink 连接层、飞行日志分析、AI 辅助调参和报告生成模块组成，用于实机连接、遥测显示、任务规划、试飞复盘和工程调参辅助。

> 当前定位：工程验证阶段的本地地面站。飞行安全相关操作必须经过真实飞控、拆桨、安全场地和 QGroundControl 对照验证。

## 一分钟上手

在 Windows 电脑上执行：

```powershell
git clone https://github.com/McDonalds-Scout/Ground-Station.git
cd Ground-Station
.\start-ui.cmd
```

启动后浏览器访问：

```text
http://127.0.0.1:8080/
```

`start-ui.cmd` 会自动完成：

- 查找可用 Python：项目 `.venv`、Codex Python、系统 Python、`py` launcher。
- 检查并安装 `requirements.txt` 中的后端依赖。
- 清理旧的地面站后台进程和端口占用。
- 启动 `ground_station_server.py` 并自动打开 UI。

如果电脑没有 Python，请先安装 Python 3.10 或更高版本，然后重新运行 `.\start-ui.cmd`。

## 推荐运行方式

只推荐使用主启动脚本：

```powershell
.\start-ui.cmd
```

不要同时手动运行 `ground_station_server.py`、`px6c_connector.py`、`mavlink_bridge.py`、`run-ai-report-server.cmd` 等旧入口，否则可能出现端口占用、串口被抢、飞控连接异常或报告服务重复启动。

## 连接方式

| 模式 | 用途 | 入口 |
|---|---|---|
| 演示模式 | 无飞控时查看 UI、姿态、地图、遥测刷新 | UI 的“连接设置” |
| USB 串口 | 使用数据线连接 PX4 / Pixhawk / PX6C | 选择 COM 口和波特率 |
| UDP 数传 | WiFi / 数传链路发送 MAVLink UDP | 默认监听 `0.0.0.0:14550` |

实机连接建议先用 QGroundControl 确认飞控、遥控器、GPS、传感器和参数本身正常，再关闭 QGC，用本 UI 连接，避免两个程序争抢同一串口。

## 当前功能状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 主界面飞行监控 | 已实现 | 地图、姿态仪、罗盘、HUD、遥测卡片、告警、MAVLink 消息 |
| MAVLink 连接 | 已实现，需按机型联调 | USB / UDP、GCS heartbeat、飞控 heartbeat、target 识别、STATUSTEXT、COMMAND_ACK |
| 实时遥测 | 已实现 | ATTITUDE、GPS、VFR_HUD、电池、空速、RC、执行器等按可用消息显示 |
| 日志记录 | 已实现 | CSV / SQLite 本地记录，输出到 `logs/` |
| 算法工程报告 | 已实现，持续校验可信度 | 基于 `.ulg` 解析，输出 Word / HTML / Markdown / PDF |
| AI 工程报告 | 已实现，依赖 API Key | 使用结构化摘要调用 OpenAI，失败时回退并说明 |
| AI PID Advisor | 已实现安全框架 | AI 只给建议，参数写入必须经过白名单、安全门、人工确认和回滚 |
| 任务规划 | 部分实现 | 支持航点编辑、保存、检查和 MAVLink mission 上传闭环，仍需更多实机覆盖 |
| 校准 / 电机 / 舵机测试 | 部分实现 | UI 和命令闭环已接入，真实执行依赖 PX4 状态、参数、传感器和安全条件 |
| Android WebView 包装 | 实验性 | `android-web-browser-app/` 保留移动端封装尝试 |

## 项目结构速览

| 路径 | 作用 |
|---|---|
| `index.html` | 前端页面结构 |
| `app.js` | 前端主逻辑：页面切换、遥测渲染、地图、姿态、接口调用 |
| `styles.css` | 前端样式 |
| `ground_station_server.py` | 主后端入口，负责静态页面、API、SSE、连接进程管理 |
| `px6c_connector.py` | MAVLink 连接主循环、遥测解析、命令发送和任务上传 |
| `services/` | 后端业务模块：AI、报告、MAVLink、校准、安全门、日志分析 |
| `core/` | 飞控状态模型和核心数据结构 |
| `config/` | AI 模型、PID 白名单、报告机型等配置 |
| `prompts/` | AI 报告和 AI 调参提示词 |
| `tests/` | 自动化测试 |
| `docs/` | 工程说明、上手指南、测试矩阵 |
| `assets/` | Logo、地图等静态资源 |
| `vendor/` | 第三方前端库，例如 Leaflet |

更详细说明见 [项目结构说明](docs/PROJECT_STRUCTURE.md)。

## AI 和密钥配置

项目不会提交真实 `.env`。如果需要启用 OpenAI / ChatGPT 能力，请复制示例配置：

```powershell
copy .env.example .env
```

然后在 `.env` 中填写自己的 Key：

```env
AI_PROVIDER=openai
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=你的 OpenAI API Key
```

安全边界：

- AI 只做分析和建议。
- AI 不直接控制飞控。
- AI 不在飞行中自动修改 PID。
- 参数写入必须经过白名单、安全门、人工确认和回滚机制。
- `.env`、日志、上传文件、生成报告不会上传 GitHub。

## 常用命令

启动地面站：

```powershell
.\start-ui.cmd
```

只检查依赖：

```powershell
.\ensure-dependencies.ps1
```

清理旧后台：

```powershell
.\cleanup-ui.ps1
```

Python 语法检查：

```powershell
python -m py_compile ground_station_server.py px6c_connector.py services\*.py core\*.py
```

运行测试：

```powershell
python -m pytest tests
```

提交代码：

```powershell
git add .
git commit -m "说明这次修改"
git push
```

## 文档入口

- [新电脑快速上手](docs/GETTING_STARTED.md)
- [项目结构说明](docs/PROJECT_STRUCTURE.md)
- [开发与提交规范](docs/DEVELOPMENT_WORKFLOW.md)
- [工程结构拆模块说明](docs/ENGINEERING_STRUCTURE.md)
- [实机测试矩阵](docs/REAL_FLIGHT_TEST_MATRIX.md)
- [MAVLink 设置说明](MAVLINK_SETUP.md)
- [遥控器设置教程](RC_SETUP_TUTORIAL.md)

## 安全提醒

任何实机操作前必须确认：

- 电机、舵机、解锁、校准测试前已拆除螺旋桨。
- 飞机处于安全测试环境。
- QGroundControl 中飞控、遥控器和传感器状态正常。
- 本 UI 显示 GCS heartbeat、Vehicle heartbeat、target_system、COMMAND_ACK 和 STATUSTEXT。
- 命令失败时先看 PX4 返回原因，不要反复强行发送。

本项目适合做公司内部地面站原型、试飞复盘工具和 MAVLink 工程验证平台；正式飞行前仍需要完整实机测试和安全评审。
