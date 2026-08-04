import subprocess
import sys
from pathlib import Path

from serial.tools import list_ports


def main():
    ports = sorted(list_ports.comports(), key=lambda item: item.device)
    if not ports:
        raise SystemExit("未发现串口。请插入数传设备后重试。")

    print("可用串口：")
    for index, port in enumerate(ports, start=1):
        print(f"  {index}. {port.device} - {port.description}")

    while True:
        raw = input("请选择数传串口编号：").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(ports):
            selected = ports[int(raw) - 1]
            break
        print("输入无效，请重新选择。")

    baud = input("请输入波特率 [57600]：").strip() or "57600"
    bridge = Path(__file__).with_name("mavlink_bridge.py")
    command = [
        sys.executable,
        str(bridge),
        "--connection",
        selected.device,
        "--baud",
        baud,
        "--vehicle",
        "UAV-01",
    ]
    print(f"启动数传：{selected.device} @ {baud}")
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
