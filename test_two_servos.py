#!/usr/bin/env python3
"""测试两个舵机：通过 esp32_servo_stream 协议（TCP）驱动 PCA9685 指定通道，其余 park 在 90°。

协议：连接 -> "ready 8" -> 8 个角度行 -> "q" -> "stopped"。

用法:
    /Users/dan/miniconda3/envs/fly/bin/python test_two_servos.py
    /Users/dan/miniconda3/envs/fly/bin/python test_two_servos.py --ch 0 1
    /Users/dan/miniconda3/envs/fly/bin/python test_two_servos.py --ch 4 6 --duration 15
    /Users/dan/miniconda3/envs/fly/bin/python test_two_servos.py --host 192.168.1.50
"""
import argparse
import socket
import sys
import time

HOST = '192.168.1.50'  # ESP32 servo stream (placeholder IP)
PORT = 8123


def main():
    parser = argparse.ArgumentParser(description='测试两个 PCA9685 舵机通道')
    parser.add_argument('--host', default=HOST,
                        help='ESP32 servo stream host (默认 %(default)s)')
    parser.add_argument('--port', type=int, default=PORT,
                        help='TCP 端口 (默认 %(default)s)')
    parser.add_argument('--ch', type=int, nargs=2, default=[0, 1],
                        help='两个通道号 (0-7, 默认 0 1)')
    parser.add_argument('--duration', type=float, default=10.0,
                        help='测试时长秒 (默认 10)')
    args = parser.parse_args()

    ch_a, ch_b = args.ch
    for c in (ch_a, ch_b):
        if not 0 <= c < 8:
            print(f"通道号必须在 0-7 范围内，得到 {c}")
            sys.exit(1)

    link = f"{args.host}:{args.port} (tcp)"

    # ── 连接 servo_stream ──────────────────────────────────────────
    print(f"连接 {link} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=8)
    except OSError as e:
        print(f"连接失败: {e}")
        sys.exit(1)
    sock.settimeout(None)  # 阻塞 I/O，与 ServoMirror 一致
    # 关 Nagle：50Hz 小包立即发出，避免攒包加剧突发（与 ServoMirror 一致）
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    rfile, wfile = sock.makefile('r'), sock.makefile('w')

    ready = rfile.readline().strip()
    if not ready.startswith('ready'):
        print(f"连接失败: {ready!r}")
        sock.close()
        sys.exit(1)
    print(f"servo_stream 就绪 ({ready})")

    def send(degs):
        wfile.write(" ".join(f"{d:.1f}" for d in degs) + "\n")
        wfile.flush()

    # 所有通道先 park 在 90°
    park = [90.0] * 8
    send(park)
    time.sleep(1.0)

    print(f"测试通道 ch{ch_a} + ch{ch_b}，交替摆动 {args.duration:.0f}s ...")
    t0 = time.time()
    toggle = False
    try:
        while time.time() - t0 < args.duration:
            degs = list(park)
            if toggle:
                degs[ch_a] = 0.0
                degs[ch_b] = 180.0
            else:
                degs[ch_a] = 180.0
                degs[ch_b] = 0.0
            send(degs)
            print(f"  ch{ch_a}={degs[ch_a]:.0f}  ch{ch_b}={degs[ch_b]:.0f}  "
                  f"(其余=90)", flush=True)
            toggle = not toggle
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        try:
            send(park)
            time.sleep(0.5)
            wfile.write("q\n")
            wfile.flush()
        except Exception:
            pass
        try:
            rfile.readline()  # expect "stopped"
        except Exception:
            pass
        try:
            wfile.close()
            rfile.close()
        except Exception:
            pass
        sock.close()
        print("完成，舵机已释放。")


if __name__ == '__main__':
    main()
