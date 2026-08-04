# AEROCENTRAL Ground Control Station实机测试矩阵

本矩阵用于把“界面可见”验证升级为“飞控真实闭环”验证。每一项都必须记录：日期、飞控型号、PX4 版本、连接方式、是否拆桨、操作者、通过/失败、失败 STATUSTEXT、COMMAND_ACK、截图或日志。

## 0. 测试前安全条件

| 编号 | 检查项 | 通过标准 | 记录 |
|---|---|---|---|
| S-01 | 螺旋桨 | 电机/舵机/解锁/校准测试前必须拆除 |  |
| S-02 | 飞机状态 | Disarmed，油门最低，安全开关状态明确 |  |
| S-03 | 电源 | 电池电压正常，USB/数传连接稳定 |  |
| S-04 | 场地 | 室内校准远离金属和强磁，室外测试有安全区域 |  |
| S-05 | 回退 | QGC 可用，必要时能立即断开本 UI 并接管 |  |

## 1. 飞控与机型覆盖

| 编号 | 机型 | 飞控 | 固件 | 连接方式 | 必测模块 |
|---|---|---|---|---|---|
| A-01 | 四旋翼 | Pixhawk 6C / PX6C | PX4 当前版本 | USB 串口 | 连接、姿态、RC、Arm、模式、电机测试、校准 |
| A-02 | 固定翼 | Pixhawk 6C / PX6C | PX4 当前版本 | USB 串口 | 连接、姿态、GPS、空速、舵机测试、Mission |
| A-03 | 复合翼 | Pixhawk 6C / PX6C | PX4 当前版本 | USB 串口 | VTOL 模式、转换阶段、舵机/电机映射 |
| A-04 | 四旋翼 | Pixhawk 6C / PX6C | PX4 当前版本 | UDP 数传 | 断联恢复、消息频率、RC、模式、告警 |
| A-05 | 固定翼 | Pixhawk 6C / PX6C | PX4 当前版本 | UDP 数传 | Mission 上传/下载、空速、地图轨迹 |

## 2. 链路稳定性测试

| 编号 | 场景 | 操作 | 通过标准 | 关键记录 |
|---|---|---|---|---|
| L-01 | USB 正常连接 | 选择串口并连接 | 5 秒内识别 target_system/target_component | HEARTBEAT、target |
| L-02 | UDP 主动连接 | 使用目标飞控 IP/端口连接 | UI 显示 GCS heartbeat 1 Hz，收到飞控 heartbeat | GCS HB、Vehicle HB |
| L-03 | UDP 监听 | 监听 0.0.0.0:14550 | 能收到飞控包并识别目标 | 连接状态 |
| L-04 | 临时拔线 | 连接后拔 USB 5 秒再插回 | UI 显示断联，重连后自动恢复数据 | 断联时间、恢复时间 |
| L-05 | 数传短断 | 关闭/遮挡数传 5 秒再恢复 | 不崩溃，不重复启动后台，恢复后 target 不乱跳 | connection status |
| L-06 | 长时间运行 | 静置连接 30 分钟 | 无后台崩溃，消息频率稳定，心跳持续 | CPU、内存、日志 |
| L-07 | 共享链路 | QGC 与 UI 先后连接同一飞控 | UI 不把 QGC heartbeat 当飞控 target | target_system |

## 3. 实时数据显示测试

| 编号 | 数据 | MAVLink 来源 | 操作 | 通过标准 |
|---|---|---|---|---|
| T-01 | 姿态仪 | ATTITUDE | 手动转动飞控 | Roll/Pitch/Yaw 低延迟变化 |
| T-02 | 罗盘 | ATTITUDE / GLOBAL_POSITION_INT / VFR_HUD | 转动飞控航向 | 航向连续变化，无明显跳变 |
| T-03 | 地速 | VFR_HUD / GLOBAL_POSITION_INT | 室外移动或仿真移动 | 地速实时变化 |
| T-04 | 高度 | GLOBAL_POSITION_INT / VFR_HUD | 抬高/仿真爬升 | 高度变化清晰 |
| T-05 | 空速 | VFR_HUD / 空速传感器 | 固定翼空速管连接 | 无空速时显示 N/A，不显示假值 |
| T-06 | 电池 | SYS_STATUS / BATTERY_STATUS | 接电池 | 电压、电流、电量真实 |
| T-07 | GPS | GPS_RAW_INT / GLOBAL_POSITION_INT | 室外定位 | fix、卫星、经纬度真实 |
| T-08 | 告警 | STATUSTEXT | 触发飞前检查失败 | 告警中心显示原文和中文说明 |

## 4. 命令闭环测试

| 编号 | 命令 | 操作 | 通过标准 | 失败时必须显示 |
|---|---|---|---|---|
| C-01 | 请求参数 | 点击请求参数列表 | 返回 PARAM_VALUE，缺失项列出 | 超时/缺失参数 |
| C-02 | Arm | 拆桨后二次确认解锁 | 显示 COMMAND_ACK，HEARTBEAT armed 状态变化 | MAV_RESULT、STATUSTEXT |
| C-03 | Disarm | 解锁后上锁 | 显示 COMMAND_ACK，HEARTBEAT disarmed | MAV_RESULT、STATUSTEXT |
| C-04 | 模式 MANUAL | 点击手动模式 | ACK 或 HEARTBEAT mode 回读确认 | 拒绝原因 |
| C-05 | 模式 POSCTL | 点击 Position | 模式正确切换 | GPS/RC/传感器原因 |
| C-06 | 模式 ALTCTL | 点击高度模式 | 模式正确切换 | 拒绝原因 |
| C-07 | 模式 LAND | 点击降落模式 | 模式命令被确认 | 拒绝原因 |
| C-08 | 模式 MISSION | 有任务后点击任务模式 | 模式切入 AUTO.MISSION | 无任务/未定位原因 |
| C-09 | 舵机测试 | 拆桨后测试每路输出 | 对应舵机单独动作，ACK 明确 | unsupported/denied |
| C-10 | 电机测试 | 拆桨后测试每路电机 | 对应电机动作，ACK 明确 | safety/unsupported |
| C-11 | 陀螺仪校准 | 静止并确认 | ACK ACCEPTED，STATUSTEXT 进度/完成 | Preflight Fail |
| C-12 | 加速度计校准 | 按六面提示操作 | PX4 提示和进度可见 | 当前姿态/移动失败原因 |
| C-13 | 磁罗盘校准 | 远离金属旋转 | MAG_CAL_PROGRESS/REPORT 可见 | 无罗盘/磁干扰 |
| C-14 | 空速校准 | 空速管静止 | ACK/STATUSTEXT 可见 | 空速无效 |

## 5. Mission GCS 测试

| 编号 | 流程 | 通过标准 |
|---|---|---|
| M-01 | 读取飞控任务 | 收到 MISSION_COUNT，并逐项读出 MISSION_ITEM_INT/ITEM |
| M-02 | 清空任务 | 收到 MISSION_ACK ACCEPTED |
| M-03 | 上传 3 点任务 | COUNT -> REQUEST -> ITEM -> ACK 完整闭环 |
| M-04 | 上传带 TAKEOFF/LAND 任务 | 命令类型正确，航点数量正确 |
| M-05 | 上传失败重试 | REQUEST 超时后能重发，不会卡死 |
| M-06 | 上传后回读校验 | 回读任务与 UI 航点一致 |
| M-07 | Mission 模式切换 | 有任务且定位正常时可切入任务模式 |
| M-08 | 任务进度显示 | 当前航点/总航点可见 |

## 6. 报告与日志测试

| 编号 | 功能 | 通过标准 |
|---|---|---|
| R-01 | USB 下载 ULG | 下载不断开连接，可断点续传 |
| R-02 | 算法报告 | 使用 verified_summary，不产生旧误报 |
| R-03 | AI 报告 | OpenAI 失败时回退说明清楚，不覆盖算法报告 |
| R-04 | 固定翼日志 | 识别固定翼，阶段不出现悬停/旋翼起降 |
| R-05 | 复合翼日志 | 阶段包含旋翼起飞、前转换、巡航、后转换、旋翼降落 |

## 7. 验收结论记录

| 日期 | 飞控/机型 | 连接 | 通过项 | 失败项 | 阻塞原因 | 下一步 |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |
