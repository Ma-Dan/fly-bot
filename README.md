# fly-bot

**fly-bot** — a fly-brain-driven quadruped spider robot.
**fly-bot** — 果蝇大脑驱动的四足蜘蛛机器人。

A 783-neuron *Drosophila* connectome model runs in real time on your Mac
(MLX / PyTorch), generates walking drive through a CPG gait controller,
and mirrors the MuJoCo simulation onto a physical Q1 Lite robot: joint
targets stream over WiFi to an ESP32-C3, which drives 8 servos through
a PCA9685 over I2C. The brain sees, smells, tastes, feels — and walks.

果蝇连接组大脑在 Mac 上实时运行，通过 CPG 步态控制器产生行走驱动，
MuJoCo 仿真与实机同步：关节指令以 50Hz 经 WiFi 流式发送到 ESP32-C3，
由 PCA9685 驱动 8 个舵机。大脑能看、能闻、能尝、能触——还能走。

实机行走演示 / Walking demo

```
Mac (Apple Silicon)                          Robot
┌──────────────────────────┐                ┌─────────────────────┐
│ Fly brain (MLX/PyTorch)  │                │  ESP32-C3           │
│   ↓ drive                │  WiFi TCP      │    ↓ I2C            │
│ CPG gait → MuJoCo sim    │ ──50Hz──────▶  │  PCA9685 → 8×MG90D  │
└──────────────────────────┘                └─────────────────────┘
```

## 1. 引用工作 / References

- **Brain & simulation framework 大脑与仿真框架**:
  [fly-brain](https://github.com/erojasoficial-byte/fly-brain)
- **Robot body kit 机器人本体套件**:
  [Jason Workshop Q1 Lite](https://jasonworkshop.com/q1lite/)

## 2. 软件环境准备 / Software Setup

Requires an Apple Silicon Mac (mjpython + MLX).
需要 Apple Silicon Mac（mjpython + MLX 后端）。

```bash
conda create -n fly python=3.10
conda activate fly
pip install -r requirements.txt
```

`requirements.txt` includes `platformio` — the ESP32 toolchain used in
section 4. First build downloads the pioarduino platform automatically.
`requirements.txt` 已包含 `platformio`（ESP32 编译工具链），首次编译会自动下载平台包。

## 3. 硬件成本与接线 / Hardware Cost & Wiring

### 3.1 成本 Cost

| 部件 Part | 价格 Price |
|---|---|
| 3D 打印身体部件（9 块）Body parts, 3D-printed | ¥26 |
| 2S 7.4V 锂电池 LiPo battery | ~¥40 |
| UBEC 电源模块（6A）Power module | ~¥30 |
| PCA9685 16 路 PWM 舵机驱动板 Servo driver | ~¥15 |
| ESP32-C3-DevKitM-1 | ~¥50 |
| MG90D 舵机 ×8 Servos | ~¥50 |
| **合计 Total** | **~¥211** |

### 3.2 接线图 Wiring

```
2S 锂电池 (7.4V) ──► UBEC IN (6A buck)
                        │
                  UBEC OUT (5V) ──┬──► PCA9685 V+    (舵机动力 servo power)
                                  └──► ESP32 5V pin  (逻辑 logic)

ESP32 GPIO4 ──► PCA9685 SDA
ESP32 GPIO5 ──► PCA9685 SCL
ESP32 3V3  ──► PCA9685 VCC     (I2C 电平参考 logic reference)

GND 共地：电池 ─ UBEC ─ ESP32 ─ PCA9685 全部连通
Common ground: battery — UBEC — ESP32 — PCA9685
```

### 3.3 舵机通道分配 / Servo Channel Map

| 通道 | 关节 | 通道 | 关节 |
|---|---|---|---|
| ch0 | FR_hip 右前髋 | ch4 | FL_hip 左前髋 |
| ch1 | FR_knee 右前膝 | ch5 | FL_knee 左前膝 |
| ch2 | RR_hip 右后髋 | ch6 | RL_hip 左后髋 |
| ch3 | RR_knee 右后膝 | ch7 | RL_knee 左后膝 |

舵机线序：橙→S（信号）、红→V+（电源）、棕→G（地）。
Servo plug: orange→signal, red→V+, brown→GND.

### 3.4 装配标定 / Mount Calibration

本机装配的舵机方向与膝中位已写入代码默认值
（`q1lite_bridge/servo_mirror.py` 的 `MOUNT_FLIP` 和 `KNEE_SPLAY_DEG`）：
4 个 hip + RR/FL 膝反向安装（scale 取负），膝中位外张 30°（低趴站姿）。
换装新舵机后可用 `--servo-map` 逐关节覆盖。

Direction and center calibration for this build are baked into the
defaults (`MOUNT_FLIP`, `KNEE_SPLAY_DEG` in `q1lite_bridge/servo_mirror.py`):
all four hips plus RR/FL knees are mounted reversed (negated scale), and
knee centers splay 30° outward for a low stance. Override per joint with
`--servo-map` after re-mounting servos.

## 4. ESP32 编译烧录 / Firmware Build & Flash

```bash
cd esp32_servo_stream

# 1. 先改固件里的 WiFi 账号密码
#    edit WIFI_SSID / WIFI_PASS in esp32_servo_stream.ino

# 2. 编译 + 烧录（USB 连接 ESP32）
#    build & flash (ESP32 on USB)
pio run -t upload

# 3. 串口监视器读 IP
#    read the IP from the serial monitor
pio device monitor
#    → "servo bridge IP: 192.168.1.45"
```

## 5. 运行 / Run

```bash
mjpython fly_embodied_q1lite.py \
    --visual --olfactory --gustatory --somatosensory \
    --monitor --mlx --servo --servo-host 192.168.1.45
```

| 旗标 Flag | 功能 Effect |
|---|---|
| `--visual` | 复眼视觉：威胁检测触发逃逸 Compound-eye vision & looming escape |
| `--olfactory` | 嗅觉：气味源趋避 Olfaction: odor attraction/avoidance |
| `--gustatory` | 味觉：糖/苦味区 Taste zones: sugar / bitter |
| `--somatosensory` | 触觉：振动感知 Somatosensory vibration sense |
| `--monitor` | 脑活动监视窗口 Brain activity monitor window |
| `--mlx` | MLX 大脑后端（Apple Silicon）MLX brain backend |
| `--servo` | 镜像关节指令到实机 Mirror joints to the real robot |
| `--servo-host` | ESP32 的 IP 地址 ESP32 IP address |

不带 `--servo` 则纯仿真运行。实机硬件验证用分组摆动测试：
Without `--servo` it runs in simulation only. Hardware bring-up:

```bash
python test_servo_groups.py --host 192.168.1.45          # 分组摆动 group sweep
python q1lite_servo_bridge.py --drive 0.4 --host 192.168.1.45  # 步态 gait test
```
