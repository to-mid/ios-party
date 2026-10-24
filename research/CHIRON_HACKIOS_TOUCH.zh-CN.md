# MIX 2 / chiron Synaptics DSX 触控覆盖层

日期：2026-10-23，Asia/Shanghai。

本轮交付包含**原生 QUP v2 FIFO 传输、只读 F12 query/control 验证器、事件生产者和测试**，不是已在实机上工作的触摸驱动。控制器与布局解析源码已补齐；剩余阻塞是退出固件后的板级交接证据，以及实际设备是否满足验证器支持的配置。当前即使选择该覆盖层并传入 `chiron_touch=1`，XNU 入口仍只打印 `BLOCKED`，不映射 MMIO、不访问 I2C、不创建轮询线程。

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

这些 DT 属性提供地址和连线；控制器操作次序来自下文固定提交的 Qualcomm 驱动，不能用 DT 名称替代。

## EDK2 能证明什么

实际阅读了本机 `/private/tmp/ios-party-research/edk2/Platforms/Msm8998FamilyPkg/Drivers/SynapticsRmi4Dxe/` 的四个文件：

- `I2cOps.c`：经 `EFI_QCOM_I2C_PROTOCOL->Read/Write` 访问一字节寄存器地址；没有实现 QUP MMIO。
- `SynapticsRmi4.h`：PDT 每项六字节、起始 `0xe9`、F12 功能号 `0x12`、页选择寄存器 `0xff`、触点读取八字节。
- `SynapticsTouchDxe.c`：向下扫描 PDT，找到 F12 后读对象前缀；首字节状态，后面 X/Y 各为小端 16 位。其 ExitBootServices 回调关闭 I2C 实例。
- `SynapticsTouchDevice.c`：板级地址、控制器编号、GPIO 和坐标范围来自 PCD；依赖 Boot Services 定位 I2C/TLMM 协议。

EDK2 路径有复位写入，本覆盖层没有复制这些写入。EDK2 只消费一个对象，还会把所有非零状态视为触摸；它没有证明实际 F12 Data1 偏移、Ctrl28 报告使能、对象数量、传感器范围或多对象排列，不能原样移植为可靠多点输入。阅读到的 chiron DSC 也不足以证明当前实机运行固件已部署该触控驱动。

EDK2 资料用于交叉核对协议边界；本轮后续取得了下述完整厂商源码，已经据其实现运行期布局发现。所有证据、上游文件和覆盖层文件的 SHA-256 均记录在 `ci/hackios/chiron_touch_sources.json`。

## 固定厂商源码

仓库 `LineageOS/android_kernel_xiaomi_msm8998`，分支引用 `lineage-22.2` 已由父任务通过 Git ref API 确认解析为提交 `7231883a368604f90806e167890e00f515a9927c`，与既有项目资料一致。读取文件时使用该不可变提交，不依赖分支后来是否移动。

| 上游路径 | 本地只读证据 | 用到的实现 |
| --- | --- | --- |
| `drivers/i2c/busses/i2c-msm-v2.c` | `/private/tmp/chiron-i2c-msm-v2.c` | `i2c_msm_qup_init`、`i2c_msm_qup_xfer_init_reset_state`、FIFO 状态切换、tag 组合、完成/错误处理 |
| `include/linux/i2c/i2c-msm-v2.h` | `/private/tmp/chiron-i2c-msm-v2.h` | QUP 寄存器偏移、状态位、服务标志、v2 tag、FIFO 大小编码 |
| `drivers/input/touchscreen/synaptics_dsx_force/synaptics_dsx_core.c` | `/private/tmp/chiron-synaptics-dsx-core.c` | F01 状态/身份、`synaptics_rmi4_f12_init`、Query5/6/8、Ctrl8/20/23/28、Data1/Data15、指头对象结构 |

头文件和 Synaptics 核心通过 GitHub Contents API 的 `Accept: application/vnd.github.raw+json` 获取；使用上述提交，下载后按字节哈希固定。没有把 Linux 的时钟/PM/pinctrl 框架直接替换成推测的裸寄存器写。

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
- PDT 发现成功不等于布局已核实，也不允许开始上报。`chiron_f12_validate` 现在实现了这个只读验证回调，并通过 `chiron_native_bind` 与传输连接。板级方向仍须由交接提供者校准，不能从 framebuffer 猜测。
- 每次轮询重新要求布局验证器检查 F01 就绪状态与 F12 布局，读完帧后再检查 F01 状态；布局改变、验证器报错或读取失败时，为所有活动槽发送一次 UP，然后停止。不会自动重试或复位面板。
- 只有验证器证实的八字节普通手指对象可进入解析器。多对象连续排列是验证器的前提，不能由八字节单点 EDK2 读取推导。未知 palm/glove/pen 状态整帧拒绝。
- 报告必须覆盖全部已验证槽位。先验证整个快照，再发事件；空槽抬手，未变化触点不重复 MOVE，UP 使用最后有效坐标。传感器最大坐标是闭区间上限，不使用 framebuffer 宽高代替。
- 如果 Query8 证明存在 Data15，则先后读取有效对象位图；不一致、越出槽位数量或读取失败时整帧拒绝。位图不含的槽位强制置空，零位图立即释放旧触点，不消费可能残留的 Data1。前后位图相同仅能排除可见的触点集合变化，并不能证明固件拥有帧锁存或排除 ABA 变化；一致性仍须实机验证。

`drivers/chiron-touch/chiron_qup.{h,c}`：

- 是可执行的原生寄存器序列，不是 UEFI 转发或回调占位；用 `read32/write32/barrier/delay_us/now_us` 对接 XNU MMIO 适配器或主机寄存器模型。`now_us` 必须提供单调的已流逝微秒，不能用请求的 sleep 时长累加来代替。
- `handoff_ready` 是必须由板级代码提供的信任边界，不是用户 boot-arg。缺少它、返回不为 1 或 MMIO 回调不完整时，不读写任何 MMIO。它必须保证整个事务期间总线独占、QUP IRQ 在 GIC 屏蔽、iface 时钟已开启且 QUP 输入时钟为 19.2 MHz、GPIO87/88 复用、触控供电/reset 和映射均已验证。
- 在获得交接后检查 master-generation/valid 位、总线闲置和 SDA/SCL、FIFO 大小；未知模式或非空闲总线在初始化写之前拒绝。
- 使用源码确认的 QUP 软件复位和初始化，FIFO 事务为 RESET→设置计数/pack-unpack→RUN→400 kHz divider→PAUSE 装入 tag→RUN→轮询完成与总线释放→读 FIFO→RESET。400 kHz divider 为上游表中的 FS=28、HT=14；没有写 GCC 时钟、TLMM 或 panel reset。
- 页选择发 `81 40 83 02 ff PAGE`；寄存器读发 `81 40 82 01 REG 81 41 87 LEN`，即无 STOP 的指针写、repeated START、读后 STOP。输入 FIFO 丢弃上游定义的两个 tag 字节。只允许地址 `0x20`、页 0..4、读最多 80 字节，且不跨 `0xff` 页选择寄存器。
- 根据硬件 `IO_MODES` 发现输入/输出容量，要求含 tag 的传输小于 FIFO 容量，与上游 FIFO 模式选择条件一致。容量不足时拒绝，绝不拆分触摸快照，也不自动退到未经实现的 BLOCK/DMA。
- 完成轮询和总线等待各有 500 ms 单调时钟预算，每次状态等待有 15 ms 预算；同时保留 5000/1500 次迭代上限，即使时钟停止推进也不会无限轮询。调度延迟或回调执行可能跨过截止时刻，因此不宣称硬实时上界；下一次检查立即超时，不再把超时的休眠当作一次正常短延时。时钟倒退也按预算失效处理。`delay_us` 运行在线程上下文，可让出 CPU，但必须返回，不能跨等待持有自旋锁或关闭中断。NACK、仲裁丢失、bus/tag/failed 错误、FIFO 错误、超时或短 FIFO 均停止会话；输出先暂存，失败不交付部分字节。错误清理只在仍有所有权且状态有效时尝试控制器 RESET，不做 GPIO bus-clear、面板复位或自动重试。

`drivers/chiron-touch/chiron_f12.{h,c}`：

- 验证 F01 正常、已 configured 且未 sleep、制造商为 1，单独读取 Query+18 的 build ID，严格限制为实测 `2676394`。固件改变时默认拒绝，不根据这个 ID 猜布局。
- Query5 长度支持 5..9，读取 control presence bitmap，以 presence 的 popcount 计算 Ctrl8/20/23/28 地址；Query6 支持最多 64 字节，逐项检查 register-size/header 与 subpacket continuation 的边界。未知 Ctrl23 subpacket、扩展 target header、缺少 mandatory prefix 或 size 不一致均拒绝。
- 读取 Ctrl8 的范围、Ctrl20 continuous mode、Ctrl23 的实际对象数量与可选字段、Ctrl28 的实际报告字段使能。要求 1..10 槽、finger 已启用、八字段报告使能正好为 `0xff`，拒绝把其它对象类型重新映射为 finger 的可选配置。未启用时只返回拒绝，**不写 Ctrl28 或 F01 configured 位**。
- Query8 长度支持 2..5；Data1 必须存在，Data0 决定 Data1 偏移；存在 Data15 时按数据 presence popcount 求地址和位图长度。Wedge/Ctrl31、其它对象类型和报告格式在本次子集之外，拒绝处理。

`drivers/chiron-touch/chiron_native.{h,c}` 把 QUP 页选择/寄存器读、绝对地址查询、F12 验证器接到既有 `chiron_dsx_bus`。`chiron_native_bind` 只有在交接检查和 QUP 初始化成功后才返回可用回调；失败不会留下一个看似可用的 bus。

`drivers/chiron-touch/chiron_xnu.c`：

- 仅可在 `CHIRON_PORT` 构建中编译，仅 `chiron_touch=1` 请求启用。
- 原生 FIFO、F12 与连接代码全部编译进原有触控对象，但当前 `chiron_native_transport()` 仍明确返回 `CHIRON_DSX_NO_TRANSPORT`，原因是缺少板级交接提供者。没有弱符号后门、自动猜测 GPIO、直接复用 UEFI 指针或未经验证的 MMIO。
- 补齐板级交接后才可调用 `chiron_native_bind`；随后线程才能执行发现、持续检查和 20 ms 轮询。目前此路径不会启动。20 ms 是处理后的等待时间，不承诺实际采样率 50 Hz。
- 已检查实际 XNU `osfmk/kern/clock.c:1573` 的延时实现：`delay_for_interval` 经 `_clock_delay_until_deadline_with_leeway`，在可调度上下文会 `assert_wait_deadline` + `thread_block`，在短间隔阈值、禁抢占或关中断时会进入 `machine_delay_until` 自旋。ARM64 `ml_delay_should_spin` 按 CPU idle latency 判断，未知 latency 返回 FALSE。因此原函数并非无条件忙等。为了让触控 worker 的行为明确，20 ms 周期现在使用 `assert_wait_timeout(..., THREAD_UNINT, 20, NSEC_PER_MSEC)`，仅在 `THREAD_WAITING` 时 `thread_block(THREAD_CONTINUE_NULL)`；worker 无持有锁的区间跨越等待，不依赖自旋阈值。
- 选择此覆盖层后，`touchscreen=1` 不会调用 redwood 的 SPI 驱动。

主机测试用合成的三槽/十槽对象快照、QUP 寄存器模型及可变长度的 DSX 寄存器窗口验证整个链路。这些测试向量**不是实机报文，也不是板级交接证明**。DSX Query/Control 寄存器存在多字节逻辑窗口，测试没有把 Query5/6/7/8 错误摊成互相覆盖的线性数组。

## 精确阻塞项

实现可用驱动前，剩下的不是缺少控制器或 DSX 源码，而是：

1. **真实 post-EBS 板级交接**：由测量或可审计的原生板级代码确认 QUP5 的映射、GCC iface/core clock 在退出固件后仍以所需速率运行，GPIO87/88 复用正确，固件/DSP 不再使用总线，以及 QUP IRQ 已屏蔽。当前 EDK2 `Close()` 的资源释放语义未证实。若需重开时钟/复用，必须先实现有证据的板级 provider；不能靠 `handoff_ready=1` 或 boot-arg 越过证明。
2. **供电、reset 与方向**：证明 PM8998 L6/L14、LAB/IBB/显示相关电源和 GPIO89 在固件退出后保持通信状态，并确认坐标朝向。当前没有这些交接后的实测结果，也没有执行 regulator、GPIO89 或显示复位写入。
3. **运行期支持子集与一致性验收**：通过原生传输读取本机真实 F01/PDT、Query5/6/8、Ctrl8/20/23/28、FIFO 容量和 Data15，确认当前配置确实满足只读验证器。若 F01 未 configured、Ctrl28 不是 `0xff`、Query6 超限、wedge 布局或 FIFO 太小，当前应拒绝；应在取得相应协议证据后单独扩展，不能静默改写控制器或拆帧。实际触摸的按下/移动/抬手、多指、帧一致性、方向与 HID 消费还须实机验收。

本任务范围内已收窄到可测试的控制器/协议子集；没有把缺少交接证据伪装成已工作的硬件驱动。

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

覆盖层只替换临时树中原 `bsd/dev/redwood_goodix.c`，并放入四个头文件以及 core/qup/f12/native 四个 include，共九个目标文件；源模式对应 `xnu/files/bsd/dev/`。新增核心均以 include 加入已有对象，因此无需改共享 `bsd/conf/files`。不改 loader、输入队列、HID 消费端或 SMP 文件，与 SMP overlay 顺序独立；kernel 模式必须在 base `CHIRON_PORT` overlay 后执行。

应用前检查原 `redwood_goodix.c`、`redwood_input.c` 的固定 SHA-256 以及全部覆盖层源哈希。任何上下文变化、已应用或目标 include 冲突都在写入前拒绝。返回报告包含九个目标文件的哈希、manifest 哈希及固定协议源码提交。状态为 `blocked-board-handoff-and-runtime-qualification`，`kernel_rebuild_required=true`、`native_i2c_transport_implemented=true`、`f12_layout_validator_implemented=true`，但 `native_i2c_hardware_enabled=false`、`hardware_touch_available=false`。

**必须重建内核，不能复用此前的单核内核缓存来声称触控代码已入包。** `chiron_touch=1` 目前只能验证日志中的拒绝路径。无需加 `touchscreen=1`。保持此实验默认关闭，保留报告的阻塞状态。

## 验证

```sh
python3 -m unittest discover -s tests -p test_chiron_touch.py -v
clang -target arm64-apple-darwin -std=c11 -ffreestanding \
  -Wall -Wextra -Werror -fsyntax-only drivers/chiron-touch/chiron_dsx.c \
  drivers/chiron-touch/chiron_qup.c drivers/chiron-touch/chiron_f12.c \
  drivers/chiron-touch/chiron_native.c
```

本机测试覆盖：

- 真实固定 HackiOS 源和 fully applied XNU 的相关上下文复制到临时目录后应用；哈希、重复应用、上下文漂移、目标冲突、非 chiron 构建的拒绝。
- 实际 C 核心以 `-Wall -Wextra -Werror -fsanitize=address,undefined` 编译执行；PDT 扫描、未知布局、短读、页选择失败、版本/描述符拒绝、未知对象及长度、全帧原子发布、多指移动与释放、所有槽位、闭区间坐标、布局变化及失败后的释放和停止。
- 原生寄存器模型验证 tag 字节流、计数、pack/unpack、状态顺序、barrier、16/80 字节读取、32/128 字节 FIFO 容量和拒绝缺少交接或单调时钟回调时任何 MMIO；测试 NACK/仲裁/bus/failed/tag/FIFO/超时/短 FIFO 错误不会交付部分数据或自动重试。额外模拟单次等待超时、时钟停滞和时钟倒退，验证截止检查和迭代兜底都能停止。
- 实际 F12 验证器测试制造商/build、F01 状态、Query5/6/8 长度和 presence/subpacket 边界、Ctrl23 数量/映射、Ctrl28 字段、continuous mode、Data0/Data15 与范围；在寄存器模型中执行 native→PDT→F12→Data1→既有事件回调，验证 Data15 对残留对象的释放、前后位图不一致和读帧期间复位的拒绝。
- XNU 适配器在主机类型/函数替身下严格编译并执行，验证新增传输/验证器确实包含在对象中，但默认关闭与显式选择均不启动缺少交接提供者的传输、不发事件；直接执行实际 sleep helper，检查其 20 ms 不可中断等待参数、`THREAD_WAITING` 时阻塞，以及已经超时时不再调用 `thread_block`。
- 私有原始采集可读时，按 manifest 校验 SHA-256，并直接解析原 runtime DT 的 I2C 基址与 GPIO，而非只匹配文档字符串。
- ARM64 Darwin freestanding 语法检查通过。

没有完整 XNU 内核构建：本地树没有 `BUILD/` 生成头与匹配的 XNU SDK。主机适配器编译和独立核心的 ARM64 语法检查不等同完整内核链接；最终真实构建由父任务执行。
