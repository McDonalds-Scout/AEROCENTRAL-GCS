# AEROCENTRAL Ground Control Station工程结构说明

本文档用于约束后续开发边界，避免所有功能继续堆进单个大文件。

## 当前运行入口

| 文件 | 职责 | 说明 |
|---|---|---|
| `ground_station_server.py` | Web 后端、API、静态页面、SSE 推送、连接进程管理 | 保留为主 Web 入口 |
| `px6c_connector.py` | MAVLink 连接进程、遥测解析、命令执行 | 后续逐步拆分 MAVLink 子模块 |
| `app.js` | 前端主界面、实时显示、页面切换 | 后续只做性能与 UI 结构优化 |

## 已拆出的模块

| 模块 | 职责 |
|---|---|
| `services/mavlink_gcs.py` | GCS HEARTBEAT、飞控 HEARTBEAT 过滤、target/source 身份 |
| `services/mavlink_command_status.py` | 命令状态文件、队列过期保护、ACK 状态归类 |
| `services/aircraft_calibration_service.py` | 飞机/传感器校准会话、安全门、PX4 文本翻译 |
| `services/rc_link_analyzer.py` | RC 链路状态、RSSI、通道健康诊断 |
| `services/rc_calibration_engine.py` | 遥控器校准采样、映射预览、参数建议 |
| `services/ulg_analyzer.py` | ULG 日志解析和算法报告数据提取 |
| `services/report_reliability.py` | 报告可信度、verified_summary、异常过滤 |
| `services/ai_report_service.py` | AI 工程报告生成 |
| `services/ai_pid_advisor.py` / `services/llm_pid_advisor.py` | AI PID 建议、本地/LLM 分析 |
| `core/vehicle_state.py` | 后端统一飞控状态对象 |
| `ui_modules/connection_diagnostics.js` | 前端连接诊断与 GCS 状态显示 |

## 后续拆分原则

1. `ground_station_server.py` 只保留 HTTP/API 调度，不直接写复杂飞控协议。
2. `px6c_connector.py` 只保留主循环、连接生命周期和调用编排。
3. MAVLink 具体协议逐步拆到 `services/mavlink_*.py`。
4. 前端实时显示优先保证飞行主界面，报告/AI/日志功能不能阻塞姿态、航向、速度显示。
5. 高风险实机命令必须有：本地安全门、命令入队状态、连接程序接管状态、COMMAND_ACK、STATUSTEXT、超时状态。
6. 任何新功能先通过语法检查和本地 API 冒烟测试，再进入实机测试矩阵。

## 下一步推荐拆分

| 优先级 | 目标模块 | 迁移内容 |
|---|---|---|
| P0 | `services/mavlink_mission.py` | Mission count/request/item/ack、上传/下载/清空/校验 |
| P0 | `services/mavlink_command_ack.py` | wait_for_command_ack、COMMAND_ACK 历史、STATUSTEXT 证据 |
| P1 | `services/mavlink_parameters.py` | 参数读取、参数写入、PARAM_VALUE 确认 |
| P1 | `services/mavlink_actuator_test.py` | 舵机/电机测试协议和安全状态 |
| P2 | `ui_modules/realtime_instruments.js` | 姿态仪、罗盘、HUD 的 requestAnimationFrame 渲染 |
