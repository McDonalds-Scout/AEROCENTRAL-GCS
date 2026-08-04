# Windows 软件打包说明

目标：把当前本地 Web 地面站打包成普通 Windows 软件，用户安装后双击桌面图标即可打开。

## 产物

构建成功后会生成：

- `dist/AEROCENTRAL/AEROCENTRAL.exe`：用户双击入口。
- `dist/AEROCENTRAL/ground_station_server.exe`：本地后端服务。
- `dist/AEROCENTRAL/px6c_connector.exe`：MAVLink/PX4 连接程序。
- `dist/AEROCENTRAL/mavlink_simulator.exe`：演示模式模拟器。
- `dist/installer/AEROCENTRALSetup-版本号.exe`：安装包，只有安装 Inno Setup 后才会生成。

## 构建命令

在项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build_windows_package.ps1
```

只生成便携版，不生成安装包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build_windows_package.ps1 -SkipInstaller
```

指定版本号：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build_windows_package.ps1 -Version 0.2.0
```

## 安装包依赖

如果需要生成 `Setup.exe` 安装包，需要先安装 Inno Setup，并确保 `ISCC.exe` 在系统 PATH 中。

如果没有 Inno Setup，脚本仍会生成可直接复制给别人使用的便携版目录：

```text
dist/AEROCENTRAL/
```

## 用户运行数据位置

安装版不会把日志、报告、上传日志、命令队列写进安装目录，而是写到：

```text
%LOCALAPPDATA%\AEROCENTRAL\
```

主要目录：

- `logs/`：飞行记录、运行日志。
- `reports/`：算法报告和 AI 报告。
- `uploads/`：用户上传的 `.ulg`。
- `downloads/`：从飞控下载的 `.ulg`。
- `commands/`：MAVLink 命令队列和 COMMAND_ACK 状态。
- `.env`：用户自己的 OpenAI Key 和本地配置，可选。

## 交付给别人前必须检查

1. 双击 `AEROCENTRAL.exe` 能打开 UI。
2. `http://127.0.0.1:8080/api/version` 能返回版本 JSON。
3. 演示模式能启动并刷新姿态、地图、告警、MAVLink 消息。
4. USB 串口能列出 COM 口。
5. UDP 连接配置能保存并启动连接进程。
6. 算法报告能导出 Word。
7. 没有 OpenAI Key 时 AI 报告应提示配置缺失或走本地回退。
8. 配置真实 OpenAI Key 后 AI 报告能调用并导出 Word。
9. 报告、日志、下载文件出现在 `%LOCALAPPDATA%\AEROCENTRAL\`。
10. 实机命令类功能必须在拆桨和安全环境中单独验证。

## 注意

- 不要把真实 `.env` 打进安装包。
- 不要把 `uploads/`、`downloads/`、`reports/`、`logs/`、`connection.log` 打进安装包。
- 软件化打包只解决分发和启动问题，不等于完成实机安全认证。
