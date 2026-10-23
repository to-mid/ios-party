# MIX 2 / chiron Synaptics DSX 触控覆盖层

日期：2026-10-23，Asia/Shanghai。

本轮交付是**有测试的协议解析、事件生产者和拒绝未知硬件的基础实现**，不是已能触摸操作的驱动。原生 XNU I2C 传输和实际 F12 query/control 布局仍是阻塞项。当前即使选择该覆盖层并传入 `chiron_touch=1`，入口也只打印 `BLOCKED`，不映射 MMIO、不访问 I2C、不创建轮询线程。

本轮没有连接设备，没有 boot、flash、reboot 或硬件寄存器操作；没有修改共享集成脚本、工作流、设备证据报告或 SMP 文件。

## 独立核对的证据

核对了 `CHIRON_LIVE_ANALYSIS.zh-CN.md`，并直接读取忽略目录下的原始采集，而不是把报告结论当作新的测量。

| 项目 | 原始证据 | 结论 |
| --- | --- | --- |
| 绑定驱动 | `chiron-live-20261020/i2c-bindings.txt` | `5-0020` 实际绑定 `synaptics_dsi_force`；`5-0038` 没有实际驱动绑定 |
| 输入设备 | 同目录 `input-devices.txt` | `synaptics_dsx` 路径为 `/devices/soc/c179000.i2c/i2c-5/5-0020/input/input1` |
| 固件 build ID | `chiron-live-details-20261020/touch-identification.txt` | `2676394`；不能据此猜测 S3330/S3331/TD4322 型号或报文布局 |
| I2C 控制器 | `runtime-tree.tar` 中 `soc/i2c@c179000/{reg,compatible}` | `qcom,i2c-msm-v2`，`0x0c179000 + 0x600`，不是 redwood 的 GENI SPI |
| I2C 引脚 | 同节点 `qcom,i2c-dat` / `qcom,i2c-clk` | TLMM GPIO87 / GPIO88 |
| 触控地址 | 同节点 `synaptics_dsi_i2c@20/reg` | 7 位地址 `0x20` |
| 复位、IRQ | 同子节点 `synaptics,reset-gpio` / `synaptics,irq-gpio` | GPIO89、GPIO125；IRQ flags `0x2008` |
| 控制器中断 | 控制器节点 `interrupts` | `<0 99 0>`；与触控 GPIO125 不同 |
| 时钟描述 | 控制器 `qcom,clk-freq-in/out` | 输入 19.2 MHz，目标 I2C 400 kHz；这不证明 XNU 接管时 GCC 时钟仍运行 |

`125` 是 TLMM GPIO 编号，不能直接送给 GIC 当作 SPI INTID。没有配置此中断。本实现未来的轮询路径固定 20 ms，不依赖 IRQ 配置或中断电平。

静态交叉检查使用用户给出的 `/private/tmp/ios-party-research/linux/`：

- `chiron-msm8998-v2.1-mtp.dts` 包含 chiron MTP 板级配置。
- `chiron-msm8998-mtp.dtsi:205` 起的 DSX 节点确认地址、reset/IRQ，以及 PM8998 L6、L14、LAB、IBB、显示相关供电依赖。
- `msm8998-blsp.dtsi:165` 起确认 BLSP1 QUP5 基址、GPIO87/88、时钟名称和目标速率。

这些 DT 属性不包含足以重建原生控制器驱动的寄存器操作次序。

## EDK2 能证明什么

实际阅读了本机 `/private/tmp/ios-party-research/edk2/Platforms/Msm8998FamilyPkg/Drivers/SynapticsRmi4Dxe/` 的四个文件：

- `I2cOps.c`：经 `EFI_QCOM_I2C_PROTOCOL->Read/Write` 访问一字节寄存器地址；没有实现 QUP MMIO。
- `SynapticsRmi4.h`：PDT 每项六字节、起始 `0xe9`、F12 功能号 `0x12`、页选择寄存器 `0xff`、触点读取八字节。
- `SynapticsTouchDxe.c`：向下扫描 PDT，找到 F12 后读对象前缀；首字节状态，后面 X/Y 各为小端 16 位。其 ExitBootServices 回调关闭 I2C 实例。
- `SynapticsTouchDevice.c`：板级地址、控制器编号、GPIO 和坐标范围来自 PCD；依赖 Boot Services 定位 I2C/TLMM 协议。

EDK2 路径有复位写入，本覆盖层没有复制这些写入。EDK2 只消费一个对象，还会把所有非零状态视为触摸；它没有证明实际 F12 Data1 偏移、Ctrl28 报告使能、对象数量、传感器范围或多对象排列，不能原样移植为可靠多点输入。阅读到的 chiron DSC 也不足以证明当前实机运行固件已部署该触控驱动。

没有在本轮取得并核实新的上游 DSX Linux 核心源码，因此没有把外部驱动的 query/control 规则写成“已核验”的板级协议。已有 EDK2 与测量文件的 SHA-256 均记录在 `ci/hackios/chiron_touch_sources.json`。

## 接入现有 HID 路径

固定 HackiOS kernel 提交：`8e61187098f2c26c5daee8f9f72b6416aa64b9ab`；XNU 为 `xnu-8796.121.2`。

已逐段追踪：

```text
原 redwood_goodix.c / 新 chiron_xnu.c
    -> redwood_touch_post(type, finger, x, y)
    -> redwood_input.c 的 /dev/redwood-touch 队列
    -> 固定 userland/src/inject.c 的 send_touch()
    -> IOHIDEventCreateDigitizerEvent / DigitizerFingerEvent
    -> IOHIDVirtualServiceClientDispatchEvent
```

已有队列的 DOWN/MOVE/UP 值分别为 1/2/3，坐标为 0..65535，用户态支持十个槽。新适配器用编译期断言检查事件编号，保持原初始化符号 `redwood_goodix_init()`，在普通内核线程中生产事件。既有消费端是否已进入当前设备的 backboardd 注入环境，仍须父任务在后续实机验收中确认；本轮没有声称 HID 事件已到达实机。

内核队列有五指长按返回 loader 的既有行为，所以帧处理先发所有 UP，再发新 DOWN/MOVE，避免槽替换时短暂多算手指。

## 新增实现边界

`drivers/chiron-touch/chiron_dsx.{h,c}`：

- 以受限回调表示页选择和 I2C 组合寄存器读。核心能要求的唯一写入是 `{0xff, page}` 页选择以及读取所需的寄存器指针，不提供复位、供电、固件、任意寄存器写接口。
- 页号上限为五页，PDT 从 `0xe9` 每次减六，必须找到每页终止项；要求唯一的 F01/F12 描述符，拒绝重复、无效基址、不支持的版本或保留位、全 `0xff`、读取失败和短读。
- PDT 发现成功不等于布局已核实，也不允许开始上报。必须由另行审计的 `validate_layout` 回调读取实际 F01/F12 状态，确认 Data1、对象步长/数量、报告字段、坐标范围及方向。当前没有这样的板级回调。
- 每次轮询重新要求布局验证器检查 F01 就绪状态与 F12 布局；布局改变、验证器报错或读取失败时，为所有活动槽发送一次 UP，然后停止。不会自动重试或复位硬件。
- 只有验证器证实的八字节普通手指对象可进入解析器。多对象连续排列是验证器的前提，不能由八字节单点 EDK2 读取推导。未知 palm/glove/pen 状态整帧拒绝。
- 报告必须覆盖全部已验证槽位。先验证整个快照，再发事件；空槽抬手，未变化触点不重复 MOVE，UP 使用最后有效坐标。传感器最大坐标是闭区间上限，不使用 framebuffer 宽高代替。

`drivers/chiron-touch/chiron_xnu.c`：

- 仅可在 `CHIRON_PORT` 构建中编译，仅 `chiron_touch=1` 请求启用。
- 当前 `chiron_native_transport()` 明确返回 `CHIRON_DSX_NO_TRANSPORT`。没有弱符号后门、自动猜测 GPIO、直接复用 UEFI 指针或未经验证的 MMIO。
- 如果以后补齐已审计的原生传输与验证器，线程路径才能执行发现、持续检查和 20 ms 轮询。目前此路径不会启动。
- 选择此覆盖层后，`touchscreen=1` 不会调用 redwood 的 SPI 驱动。

主机测试用合成的三槽/十槽对象快照验证事件语义。这些测试向量**不是实机报文，也不是允许本机通过布局验证的白名单**。

## 精确阻塞项

实现可用驱动前，需要补齐以下证据与实现：

1. **原生 post-EBS BLSP1 QUP5 传输**：可审计的 MSM8998 QUP/I2C-v2 控制器源码或可靠协议资料，包含复位/状态切换、FIFO/tag 格式、组合事务与 repeated START/STOP、错误和超时处理；还需确认 GCC iface/core clock 的寄存器及操作次序、TLMM GPIO87/88 的实际复用配置、所有权和安全的接管方式。只有基址与 DT 时钟名字不够。redwood 的 SM7325/SC7280 GENI、TLMM 地址和位操作不能搬到 MSM8998。
2. **供电与固件交接状态**：明确测量或交接协议证明触控电源和 resetGPIO89 在退出固件后保持可通信状态，并确认 EDK2 的 `Close()` 是否关掉所需资源；否则先实现有证据的板级供电接管，不能盲目复位或写 regulator。
3. **真实 F12 布局**：本机 PDT/F01 标识与状态、F12 query/control 的原始读取结果，以及经过核对的厂商解析源码。需确认 Data1 偏移、Ctrl28 已启用字段、对象类型/步长/数量、Ctrl8 或等效坐标范围、方向，以及完整快照的一致读取与复位检测语义。只有固件 build ID 或 EDK2 单个八字节读取不够。

优先补齐 1、2 后通过受限寄存器读取取得 3；不能为收集 3 而在本轮使用未经核验的 I2C 控制器写操作。

## 父任务集成

所有仓库内改动均在本任务声明的新文件范围。父任务负责选择实验开关、重新构建、镜像来源和工作流。当前不应把此功能描述为“触摸已启用”。

推荐只在已执行原有 HackiOS patch 与 base chiron kernel overlay 的**临时 XNU 树**上应用：

```sh
python3 ci/hackios/apply_chiron_touch.py kernel \
  --source "$XNU_TREE" --report "$EVIDENCE/chiron-touch-overlay.json"
```

Python API：

```python
import apply_chiron_touch
report = apply_chiron_touch.apply(tree=xnu_tree)
# 等价：kernel_overlay(xnu_tree)
```

另一种互斥方式是在 HackiOS 临时源仓库运行 `source_overlay(source_repo)` / `apply(source=source_repo)`，或 CLI 的 `source` 模式，之后再执行上游 `xnu/apply.sh`。**两种方式只能选一种**；不要先源覆盖再内核覆盖。

覆盖层只改临时树中原 `bsd/dev/redwood_goodix.c` 的实现，并放入同目录 `chiron_dsx.h`、`chiron_touch_core.inc`；源模式对应 `xnu/files/bsd/dev/`。核心以 include 加入已有对象，因此无需改共享 `bsd/conf/files`。不改 loader、输入队列、HID 消费端或 SMP 文件，与 SMP overlay 顺序独立；kernel 模式必须在 base `CHIRON_PORT` overlay 后执行。

应用前检查原 `redwood_goodix.c`、`redwood_input.c` 的固定 SHA-256 以及全部覆盖层源哈希。任何上下文变化、已应用或目标 include 冲突都在写入前拒绝。返回报告包含三个目标文件的哈希、manifest 哈希、`kernel_rebuild_required=true`、`native_i2c_transport_implemented=false`、`hardware_touch_available=false`。

**必须重建内核，不能复用此前的单核内核缓存来声称触控代码已入包。** `chiron_touch=1` 目前只能验证日志中的拒绝路径。无需加 `touchscreen=1`。保持此实验默认关闭，保留报告的阻塞状态。

## 验证

```sh
python3 -m unittest discover -s tests -p test_chiron_touch.py -v
clang -target arm64-apple-darwin -std=c11 -ffreestanding \
  -Wall -Wextra -Werror -fsyntax-only drivers/chiron-touch/chiron_dsx.c
```

本机测试覆盖：

- 真实固定 HackiOS 源和 fully applied XNU 的相关上下文复制到临时目录后应用；哈希、重复应用、上下文漂移、目标冲突、非 chiron 构建的拒绝。
- 实际 C 核心以 `-Wall -Wextra -Werror -fsanitize=address,undefined` 编译执行；PDT 扫描、未知布局、短读、页选择失败、版本/描述符拒绝、未知对象及长度、全帧原子发布、多指移动与释放、所有槽位、闭区间坐标、布局变化及失败后的释放和停止。
- XNU 适配器在主机类型/函数替身下严格编译并执行，验证默认关闭与显式选择均不启动缺失的传输、不发事件。
- 私有原始采集可读时，按 manifest 校验 SHA-256，并直接解析原 runtime DT 的 I2C 基址与 GPIO，而非只匹配文档字符串。
- ARM64 Darwin freestanding 语法检查通过。

没有完整 XNU 内核构建：本地树没有 `BUILD/` 生成头与匹配的 XNU SDK。主机适配器编译和独立核心的 ARM64 语法检查不等同完整内核链接；最终真实构建由父任务执行。
