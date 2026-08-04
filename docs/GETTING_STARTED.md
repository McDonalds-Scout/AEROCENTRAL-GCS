# 新电脑快速上手

这份文档给第一次接手项目的人使用，目标是从 GitHub 拉下代码后能尽快启动 UI，并知道下一步该验证什么。

## 1. 环境准备

推荐环境：

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / Windows 11 |
| Python | 3.10 或更高版本 |
| Git | 可从 GitHub clone / pull / push |
| 浏览器 | Chrome / Edge |
| 实机测试 | PX4 / Pixhawk / PX6C 兼容飞控，USB 或 UDP MAVLink |

Python 可以来自任意一种：

- 项目 `.venv\Scripts\python.exe`
- Codex 自带 Python
- 系统安装的 `python`
- Windows `py` launcher

启动脚本会自动查找这些位置。

## 2. 下载项目

```powershell
git clone https://github.com/McDonalds-Scout/Ground-Station.git
cd Ground-Station
```

不要手动复制别人电脑里的 `logs/`、`uploads/`、`reports/`、`.env`。这些是个人本地数据和密钥。

## 3. 启动 UI

```powershell
.\start-ui.cmd
```

成功后会看到：

```text
URL: http://127.0.0.1:8080/
```

浏览器如果没有自动打开，手动访问：

```text
http://127.0.0.1:8080/
```

## 4. 如果没有 Python

安装 Python 3.10 或更高版本后重新运行：

```powershell
.\start-ui.cmd
```

也可以手动创建虚拟环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start-ui.cmd
```

## 5. 配置 AI 功能

AI 调参和 AI 报告需要 `.env`：

```powershell
copy .env.example .env
```

然后填写：

```env
OPENAI_API_KEY=你的 OpenAI API Key
```

没有 Key 时，本地工程规则、UI、MAVLink 连接、算法报告仍可使用；OpenAI 相关功能会失败或回退。

## 6. 先用演示模式验证

第一次运行建议不要直接连接实机：

1. 打开 UI。
2. 进入“连接设置”。
3. 选择“演示模式”。
4. 点击“开始连接”。
5. 确认地图、姿态仪、罗盘、HUD、趋势图有数据刷新。

演示数据不代表真实 GPS 或真实飞机状态。

## 7. USB 实机连接

1. 关闭 QGroundControl，避免占用串口。
2. 使用数据线连接飞控。
3. 打开“连接设置”。
4. 选择 USB 串口连接。
5. 选择正确 COM 口。
6. 波特率通常使用 `57600` 或 `115200`。
7. 点击“开始连接”。

确认 UI 中至少能看到：

- Vehicle heartbeat received
- target_system / target_component
- 姿态数据变化
- STATUSTEXT
- 电池 / GPS / 飞行模式按飞控实际状态显示

## 8. UDP 数传连接

常见方式：

- UI 监听地址：`0.0.0.0`
- UI 监听端口：`14550`
- 飞控或数传设备向电脑发送 MAVLink UDP

如果使用目标飞控 IP，请确认电脑和飞控在同一网段，防火墙没有拦截 UDP。

## 9. 常见问题

### 浏览器打不开

先运行：

```powershell
.\cleanup-ui.ps1
.\start-ui.cmd
```

确认终端里显示 `http://127.0.0.1:8080/`。

### 依赖安装失败

检查 Python 和 pip：

```powershell
python --version
python -m pip --version
```

如果公司网络限制 pip，请配置代理或使用公司内网 Python 镜像源。

### 串口连接失败

- 关闭 QGC。
- 拔插 USB。
- 刷新 COM 口。
- 尝试 `57600` 和 `115200`。
- 查看 Windows 设备管理器里的串口名称。

### UI 有数据但命令失败

说明遥测接收可能正常，但命令闭环不一定正常。需要检查：

- GCS heartbeat 是否发送。
- target_system / target_component 是否正确。
- COMMAND_ACK 是否返回。
- STATUSTEXT 是否有 PX4 拒绝原因。
- 当前是否处于实机指令模式。

## 10. 接手开发前先读

- [项目结构说明](PROJECT_STRUCTURE.md)
- [开发与提交规范](DEVELOPMENT_WORKFLOW.md)
- [实机测试矩阵](REAL_FLIGHT_TEST_MATRIX.md)
