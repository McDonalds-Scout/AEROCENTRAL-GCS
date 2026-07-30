# RC 设置教程

本教程用于当前无人机地面站 UI 的“RC 设置”页面。该功能参考 QGroundControl Radio Setup，但必须以安全为先：只读取 `RC_CHANNELS` 和 PX4 RC 参数，写入前必须人工确认，不发送 `RC_OVERRIDE`、不发送 `MANUAL_CONTROL`、不自动解锁。

## 1. 准备工作

1. 拆除螺旋桨。
2. 打开遥控器，并确认接收机已经和飞控连接。
3. 用 USB、数传或 UDP 连接飞控。
4. 打开 UI：`http://127.0.0.1:8080/`。
5. 左侧进入“连接设置”，确认 MAVLink 已连接并收到 heartbeat。
6. 左侧进入“RC 设置”。

## 2. 和 QGC 对比

先在 QGC 的 Radio 页面确认遥控器正常，再打开本 UI 的“RC 设置 / RC Monitor”。

对比重点：

- QGC 的 CH1-CH18 数值应和本 UI 的 CH1-CH18 基本一致。
- 拨动 Roll / Pitch / Throttle / Yaw 时，两边变化的通道应一致。
- 如果 QGC 有数值、本 UI 没数值，优先检查 MAVLink 是否收到 `RC_CHANNELS`。
- 如果本 UI 的 mapped control 不对，优先检查 `RC_MAP_ROLL`、`RC_MAP_PITCH`、`RC_MAP_THROTTLE`、`RC_MAP_YAW`。

注意：`RC_MAP_*` 是 1-based 通道编号。例如 `RC_MAP_ROLL=1` 表示 `CH1`，对应程序数组里的 `channels[0]`。

## 3. RC Monitor 页面说明

RC Monitor 会显示：

- Connected / Disconnected
- Vehicle heartbeat
- GCS heartbeat
- target system / component
- UI refresh rate
- CH1 到 CH18 的 PWM
- 每路百分比
- 每路当前 `MIN / MAX / TRIM / REV`
- Roll / Pitch / Throttle / Yaw / Flight Mode / Arm Switch 映射

如果显示 `No RC`：

1. 确认飞控真的连接。
2. 确认遥控器打开。
3. 确认 QGC 能看到 RC。
4. 确认本 UI 的 MAVLink 连接不是旧连接或错误端口。

## 4. RC Calibration Wizard 使用流程

进入“RC 设置”，切到 `Calibration Wizard`。

1. 点击“开始 RC 校准会话”。
2. Step 1 检测遥控器：确认至少 5 路有效通道。
3. Step 2 中位采样：松开所有摇杆，油门保持最低，点击“采样当前步骤”。
4. Step 3 Roll：左右打满 Roll，再回中，点击采样。
5. Step 4 Pitch：前后打满 Pitch，再回中，点击采样。
6. Step 5 Throttle：油门最低到最高，再回最低，点击采样。
7. Step 6 Yaw：左右打满 Yaw，再回中，点击采样。
8. Step 7 min/max：移动所有摇杆、开关、旋钮到完整行程，点击采样。
9. Step 8 Flight Mode：拨动飞行模式开关所有档位，点击采样。
10. Step 9 Arm Switch：拨动解锁开关，点击采样。
11. 点击“生成预览”。
12. 检查参数 diff。
13. 确认无误后输入 `确认写入RC参数`。
14. 点击“写入 PX4”。

## 5. 参数写入说明

写入前 UI 会备份当前 RC 参数，主要包括：

- `RC_MAP_ROLL`
- `RC_MAP_PITCH`
- `RC_MAP_THROTTLE`
- `RC_MAP_YAW`
- `RC_MAP_FLTMODE`
- `RC_MAP_ARM_SW`
- `RC1_MIN` 到 `RC18_MIN`
- `RC1_MAX` 到 `RC18_MAX`
- `RC1_TRIM` 到 `RC18_TRIM`
- `RC1_REV` 到 `RC18_REV`
- `RC1_DZ` 到 `RC18_DZ`

写入完成后，UI 会重新请求 RC 参数。你需要再次检查 RC Monitor，确认参数变化已经读回。

## 6. 恢复备份

如果写入后发现遥控器方向或映射异常：

1. 保持飞控连接。
2. 确认飞控未解锁。
3. 在恢复输入框输入 `确认恢复RC参数`。
4. 点击“恢复备份”。
5. 等待队列执行后，再刷新 RC Monitor。

## 7. 安全注意事项

- 飞控 Armed 时禁止 RC 校准和写入。
- 飞行中禁止写 RC 参数。
- 写入前必须检查 diff。
- UI 不会自动解锁。
- UI 不发送 `RC_OVERRIDE`。
- UI 不发送 `MANUAL_CONTROL`。
- UI 不控制电机或舵机。
- 不确定识别结果时，不要写入，重新采样或手动检查。

## 8. 常见问题

### QGC 正常，本 UI 没有 RC 数据

检查 UI 的 MAVLink 连接是否收到 `RC_CHANNELS`。如果只收到姿态/GPS，没有收到 `RC_CHANNELS`，说明消息流没有请求成功或链路配置不同。

### 通道和 QGC 不一致

检查是否连到了同一个飞控和同一条链路。RC 原始通道必须来自 `RC_CHANNELS`，不能用 `SERVO_OUTPUT_RAW` 或执行器输出替代。

### 写入按钮没反应

确认：

- 当前模式允许写参数。
- 飞控未解锁。
- 输入的确认文本是 `确认写入RC参数`。
- 浏览器页面是最新版本，必要时按 `Ctrl + F5`。

### 油门最低不是 0%

检查 `RC_MAP_THROTTLE` 指向的通道是否正确，以及对应 `RCx_MIN / RCx_MAX / RCx_TRIM` 是否合理。油门的 `TRIM` 通常应接近 `MIN`。
