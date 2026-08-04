# 开发与提交规范

这份文档用于让新同事知道如何修改项目、如何验证、如何提交，避免把地面站写崩或把实机安全风险带进代码。

## 1. 开发前确认

开始修改前先运行：

```powershell
git status
```

如果有别人未提交的修改，不要直接覆盖。先确认修改内容和当前任务是否相关。

## 2. 推荐修改顺序

1. 先看 `README.md` 和 `docs/PROJECT_STRUCTURE.md`。
2. 明确要改的是前端、后端、MAVLink、报告还是 AI。
3. 小范围修改，不做无关重构。
4. 每次改完先跑语法检查。
5. 再启动 UI 做本地冒烟测试。
6. 涉及实机命令时，按 `docs/REAL_FLIGHT_TEST_MATRIX.md` 记录结果。

## 3. 常用验证命令

Python 语法检查：

```powershell
python -m py_compile ground_station_server.py px6c_connector.py services\*.py core\*.py
```

运行测试：

```powershell
python -m pytest tests
```

启动 UI：

```powershell
.\start-ui.cmd
```

检查后端接口：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/version
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/connection/status
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/telemetry
```

## 4. Git 提交

提交前查看改动：

```powershell
git status
git diff
```

提交并推送：

```powershell
git add .
git commit -m "简短说明本次修改"
git push
```

不要提交：

- `.env`
- API Key
- `.ulg`
- `logs/`
- `uploads/`
- `downloads/`
- `reports/`
- `outputs/`
- 本地构建工具和临时文件

这些已经由 `.gitignore` 排除。如果发现仍然出现在 `git status`，先停下来处理 `.gitignore`。

## 5. 实机安全规则

任何涉及以下操作的修改，都必须按实机测试矩阵验证：

- Arm / Disarm
- 飞行模式切换
- 电机测试
- 舵机测试
- 任务上传
- 参数写入
- 飞机/传感器校准
- RC / 遥控器相关功能

最低安全要求：

- 拆除螺旋桨。
- 飞机处于 Disarmed。
- 油门最低。
- QGC 对照正常。
- UI 能显示 COMMAND_ACK 和 STATUSTEXT。
- 命令失败时显示 PX4 拒绝原因。

## 6. AI 功能边界

AI 可以做：

- 日志解释。
- PID 建议。
- 报告文字生成。
- 风险提示。
- 试飞复盘建议。

AI 不允许做：

- 直接控制飞控。
- 飞行中自动改 PID。
- 自动 Arm / Disarm。
- 自动切模式。
- 绕过安全门写参数。

所有 AI 输出都必须经过本地工程规则、安全检查和人工确认。

## 7. 推荐拆模块方向

当前仍有几个大文件：

- `app.js`
- `ground_station_server.py`
- `px6c_connector.py`

后续拆分建议：

- 前端仪表和地图拆到 `ui_modules/`。
- MAVLink mission 拆到 `services/mavlink_mission.py`。
- 参数读写拆到 `services/mavlink_parameters.py`。
- 电机/舵机测试拆到 `services/mavlink_actuator_test.py`。
- COMMAND_ACK 和 STATUSTEXT 证据链继续独立化。

拆模块时必须保证现有启动脚本和 API 路径不变，避免 UI 入口失效。
