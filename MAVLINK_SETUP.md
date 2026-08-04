# MAVLink 数传接入

## 数据链路

飞控 -> 数传电台 -> 电脑串口或 UDP -> `mavlink_bridge.py` -> UI

Pixhawk 6C/PX6C 使用专用连接程序 `px6c_connector.py`。

UI 地址：

```text
http://127.0.0.1:8080
```

## 1. 安装 MAVLink 依赖

依赖已配置。需要重装时双击或运行：

```powershell
.\install-mavlink.cmd
```

## 2. 串口数传

插入数传设备后运行：

```powershell
.\start-serial.cmd
```

程序会列出串口及设备名称，再选择数传对应的端口。波特率必须与数传模块设置一致，常见值为 `57600` 或 `115200`。

Pixhawk 6C 专用连接可直接运行：

```powershell
.\start-px6c.cmd
```

选择 `1` 自动识别 USB 数传，选择 `2` 使用 UDP `14550`。

## 3. UDP 数传

飞控或地面站向本机 UDP `14550` 发送 MAVLink 时运行：

```powershell
.\start-mavlink.cmd udpin:0.0.0.0:14550
```

## UI 使用的 MAVLink 消息

- `HEARTBEAT`：判断飞控在线。
- `GLOBAL_POSITION_INT`：经纬度、绝对高度、相对高度、航向。
- `VFR_HUD`：地速和航向。
- `SYS_STATUS`：剩余电量。
- `GPS_RAW_INT`：卫星数和定位类型。
- `RADIO_STATUS`：数传链路信号。

## VS Code 一键运行

在“运行和调试”列表中选择：

- `运行 UI + MAVLink 模拟测试`：验证完整系统。
- `运行 UI + UDP 数传`：连接 UDP `14550`。
- `3. MAVLink 串口自动选择`：扫描并选择串口数传。
- `运行 UI + PX6C UDP`：使用 Pixhawk 6C/PX4 专用连接器。
- `PX6C 自动连接（串口数传）`：自动忽略蓝牙虚拟串口并连接 USB 数传。

## 地图坐标

飞控 GPS 通常输出 WGS-84 坐标，UI 直接使用 WGS-84 绘制。当前地图瓦片需要网络；如需完全离线使用，可将瓦片地址替换为本地地图服务器。
