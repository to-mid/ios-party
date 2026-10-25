# MIX 2 / chiron Synaptics DSX 触控覆盖层

日期：2026-10-23，Asia/Shanghai。

本轮交付包含**原生 QUP v2 FIFO、F12 验证器、只读板级探针、条件放行 provider、loader 所有权检查和测试**，尚未经过实机验收。默认关闭。`chiron_touch=1` 在严格身份、保留硬件和交接条件全部满足后才初始化 QUP；不满足时打印具体寄存器、期待 mask/value 和拒绝原因。`chiron_touch_probe=1` 优先执行同步只读诊断，然后在 GUI 前触发带快照的原生 panic，**不创建 worker、不调用 native bind、不打开或复位 QUP**。

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
| GCC / TLMM 范围 | runtime DT 的 `soc/qcom,gcc@100000/reg`、`soc/pinctrl@03400000/reg` | `0x00100000 + 0xb0000`、`0x03400000 + 0xc00000`，与本地 EDK2 memory map 相符 |

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
| `drivers/clk/msm/clock-gcc-8998.c`、`include/dt-bindings/clock/msm-clocks-hwio-8998.h` | `/private/tmp/chiron-clock-gcc-8998.c`、`chiron-msm-clocks-hwio-8998.h` | QUP5/AHB 的寄存器、AP vote17、XO=19.2 MHz 与 divider 编码 |
| `drivers/clk/msm/clock-local2.c` | `/private/tmp/chiron-clock-local2.c` | branch enable/off、root update/dirty/off、HID divider/source/mode mask |
| `drivers/pinctrl/qcom/pinctrl-msm8998.c` | `/private/tmp/chiron-pinctrl-msm8998.c` | EAST tile、GPIO87/88 的 function index3、GPIO mux/OE/input/output 位 |
| `drivers/input/touchscreen/synaptics_dsx_force/synaptics_dsx_i2c.c` | `/private/tmp/chiron-synaptics-dsx-i2c.c` | DT 的 swap/x-flip/y-flip 布尔默认值 |

新增上游文件通过 GitHub Contents API 的 `Accept: application/vnd.github.raw+json` 获取，使用上述提交，下载后按字节哈希固定。该下游树的 GCC 文件实际位于 `drivers/clk/msm/clock-gcc-8998.c`，不是上游主线的 `drivers/clk/qcom/gcc-msm8998.c`。没有把 Linux 的时钟/PM/pinctrl 框架替换成未经验证的裸寄存器写。

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
- `handoff_ready` 由新增板级 provider 提供，不是可直接强制为真的 boot-arg。底层缺少它、返回不为 1 或 MMIO 回调不完整时，不访问 QUP。provider 自身会只读 GCC/TLMM/GIC，检查保留条件；初次还检查 QUP idle/master 状态。所有权假设另见下节，并没有声称 Boot Services 已退出。
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

- 仅可在 `CHIRON_PORT` 构建中编译，正常驱动默认关闭，`chiron_touch=1` 才请求启用。
- `chiron_native_transport()` 现在执行真实 board probe，成功后构造 MMIO/barrier/单调时钟/调度等待回调并调用 `chiron_native_bind`，不再是无条件 stub。写回调只能指向已映射 QUP 窗口；没有写 GCC、GPIO、reset 或 regulator 的接口。
- 只有满足全部条件的 native bind 才能初始化 QUP，随后线程发现 PDT、验证 F12 并轮询。未知硬件条件拒绝；原有 `chiron_touch=2` 保留为异步只读记录模式，不打开 QUP。
- 拍照诊断采用新的 `chiron_touch_probe=1`，优先于正常触控开关，在初始化线程中同步检查并 panic，不启动 worker。即使所有条件通过也不调用 native bind。panic 含原始快照与 `obs` 有效位图，未读单元为零占位；完整普通日志只列实际读到的寄存器。平台不符也只记录拒绝并 panic，MMIO 映射数为零。
- 已检查实际 XNU `osfmk/kern/clock.c:1573` 的延时实现：`delay_for_interval` 经 `_clock_delay_until_deadline_with_leeway`，在可调度上下文会 `assert_wait_deadline` + `thread_block`，在短间隔阈值、禁抢占或关中断时会进入 `machine_delay_until` 自旋。ARM64 `ml_delay_should_spin` 按 CPU idle latency 判断，未知 latency 返回 FALSE。因此原函数并非无条件忙等。为了让触控 worker 的行为明确，20 ms 周期现在使用 `assert_wait_timeout(..., THREAD_UNINT, 20, NSEC_PER_MSEC)`，仅在 `THREAD_WAITING` 时 `thread_block(THREAD_CONTINUE_NULL)`；worker 无持有锁的区间跨越等待，不依赖自旋阈值。
- 选择此覆盖层后，`touchscreen=1` 不会调用 redwood 的 SPI 驱动。

主机测试用合成的三槽/十槽对象快照、QUP 寄存器模型及可变长度的 DSX 寄存器窗口验证整个链路。这些测试向量**不是实机报文，也不是板级交接证明**。DSX Query/Control 寄存器存在多字节逻辑窗口，测试没有把 Query5/6/7/8 错误摊成互相覆盖的线性数组。

## 板级只读检查

`drivers/chiron-touch/chiron_board.{h,c}` 首先检查 ADT：`/arm-io/device_type` 必须逐字节为 `chiron\0`；`ranges` 必须为 `{0,0x01000000,0xff000000}`；GIC offsets/size 必须与 chiron 的 `0x17a00000` distributor、`0x17b00000` redistributor 一致；`/chosen/display-rotation` 必须为 0。iOS 的伪装 model/chip-id 不用作板级身份。任何身份、长度或 rotation 不符都在映射 MMIO 前拒绝。

通过身份检查后，按以下次序读取。所有地址、mask 和期待值来自上表固定源码，**寄存器当前值尚未实机测量**。

| 检查 | 物理地址 | mask / 期待值 |
| --- | --- | --- |
| BLSP1 reset 未置位 | `0x00117000` | `0x1 / 0` |
| AP AHB vote 保持 | `0x00152004` | `0x20000 / 0x20000` |
| AHB branch 未 off | `0x00117004` | `0x80000000 / 0` |
| QUP5 I2C branch 开启、非 HW gating、未 off | `0x00121008` | `0x80000003 / 1` |
| Root 未 off/update/dirty | `0x00121020` | `0x800000f1 / 0` |
| XO source、divide-by-one、MND off | `0x00121024` | `0x371f / 1` |
| GPIO87/88 复用为 blsp_i2c5 | `0x03d57000`、`0x03d58000` | `0x3c / 0xc` |
| GPIO89 为 GPIO output | `0x03d59000` | `0x23c / 0x200` |
| reset output 与实际输入均高 | `0x03d59004` | `0x3 / 3` |
| QUP IRQ131 已屏蔽 | `0x17a00110` | `0x8 / 0`，SPI99+32=131 |
| QUP master/valid，非 PAUSE | `0x0c179004` | `0x14 / 0x14`，且 bit1=0 |
| QUP 无错误/活动总线/master，SDA/SCL 高 | `0x0c179404` | `0x0f8003fc / 0x0c000000` |

GCC 使用运行时 DT 的基址与大小。GPIO 地址按 `TLMM 0x03400000 + EAST 0x900000 + GPIO*0x1000` 计算，没有套用 redwood 的平铺地址。IRQ mask 读取沿用本 XNU GICv3 的 ISENABLER 计算，没有修改中断路由或使能位。

GCC 检查失败时不会访问后面的 TLMM/GIC/QUP；pin/reset 失败时不访问 QUP；mask 失败也不访问 QUP。日志含 `status`、失败寄存器名、实际值和期待 mask/value。映射失败明确记录 `cannot map/read prerequisite`，不会把未读零值说成测量。没有尝试读取未核实的 PMIC regulator 寄存器；panel 是否可工作还由后续 F01/F12 应答确认，而不是宣称测到了供电电压。

所有权未确认时，最多保留上述已核实地址的 GCC/TLMM/GIC 只读快照；这些条件通过后记录 `CB_OWNERSHIP`，在访问任何 QUP 寄存器（包括 STATE/STATUS）前拒绝。不能把此前的 GCC 快照当作固件 CPU 不会随后关闭 QUP 时钟的保证；QUP 的 observed 位保持未读，且没有寄存器写入。

每次事务前重新检查 GCC/pin/reset/GIC 保持条件。此时不要求我们自己正在使用的 QUP idle；传输状态由 QUP 核心检查。条件丢失后停止，不再为了清理而向时钟已关闭的 QUP 写 RESET。

## 所有权假设与边界

**纠正此前 post-EBS 表述**：固定 HackiOS `xnuloader/loader.c` 的 `loader_save_ctx` / `jump_to_kernel` 路径和 XNU `loader_return.c` 明确保留 Boot Services，未调用 ExitBootServices。EDK2 Synaptics 的 EBS Close 回调不能当作已经执行。

`loader_overlay(repo)` 现在加入可执行检查，而不是凭一个用户 flag 宣称拥有总线：

- 只有 chiron、SMC、实际 boot affinity=0、kernel affinity 与 boot 相同的 direct-entry 路径才可能放行。
- PSCI 版本必须为 0.2 或 1.x；逐个读取实测七个其它 MPIDR `{1,2,3,0x100,0x101,0x102,0x103}`，每个必须明确返回 OFF。未知、ON、ON_PENDING、NOT_SUPPORTED 都不给所有权标志。
- 在 ADT `/chosen/chiron-touch-handoff` 写入四个 u32：版本、direct-entry 条件、已确认 OFF 的其它 CPU mask、`firmware-resident`。没有启用触控或探针时不进行这些 PSCI 查询。最后跳入内核前再次验证；条件改变则拒绝此次 boot，而不是留一个过期标志。
- 同核 `jump_to_kernel` 首先屏蔽中断，替换执行环境后由 XNU 接管异常向量；resident firmware 的 boot CPU 被挂起，其余核已 OFF，没有 CPU 执行固件 DSX 20 ms 定时回调。不同 boot CPU 的并行 UEFI 路径不会取得此标志。

**这是源码支持的正常世界固件执行隔离假设，不是硬件总线锁，也不是 EBS 证明。** 普通 OS 驱动同样要假设 secure world、DSP 或其它独立 master 不会并发占用 AP 的 QUP5；本轮没有测量这些不可见的 master。后续若出现反例，必须撤销该假设、补资源所有权交接，不能用 idle 一次读数代替互斥。loader 不调用 CPU_ON/OFF、不停止 DXE、不改变 pinmux/clock/regulator，也不伪造 EBS。

方向使用同一固定驱动中 `of_property_read_bool` 的默认 false，已直接核对 runtime DT 不含 `synaptics,swap-axes/x-flip/y-flip`，并要求 ADT rotation=0；实际触摸方向仍须实机验收。

剩余实机条件是保留时钟/引脚/reset/IRQ 的实际值、可用 FIFO 容量、F01/F12 是否属于所支持子集，以及真实按下/移动/抬手、多指与 HID 消费行为。若时钟关闭、复用不符、F01 未 configured、Ctrl28 不是 `0xff`、Query6 超限、wedge 布局或 FIFO 太小，本版本明确拒绝，不盲目补写硬件。

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
loader_report = apply_chiron_touch.loader_overlay(hackios_repo)
report = apply_chiron_touch.apply(tree=xnu_tree)
# 等价：kernel_overlay(xnu_tree)
```

loader API 已分别验证 base-only 和实际 phone 顺序：base chiron → `apply_chiron_usb.apply` → `apply_chiron_memory.apply` → `loader_overlay`。两种变体都将整个 `loader.c` 编译为 ARM64 Windows COFF 对象，而不只是做语法检查；include 名称和局部变量也不与 SMP overlay 冲突。父任务已有分发可直接调用。它只修改临时树的 `xnuloader/loader.c`，并加入本任务的 `chiron_touch_loader.inc` 与 ABI header，不改原始固定源仓库或共享集成文件。

另一种互斥方式是在 HackiOS 临时源仓库运行 `source_overlay(source_repo)` / `apply(source=source_repo)`，或 CLI 的 `source` 模式，之后再执行上游 `xnu/apply.sh`。**两种方式只能选一种**；不要先源覆盖再内核覆盖。

kernel 覆盖层只替换临时树中原 `bsd/dev/redwood_goodix.c`，并放入 dsx/qup/f12/native/board 的头文件和 include、共享 ABI header，共十二个目标文件；源模式对应 `xnu/files/bsd/dev/`。新增核心以 include 加入已有对象，无需改共享 `bsd/conf/files`。不改输入队列、HID 消费端或 SMP 文件；kernel 模式必须在 base `CHIRON_PORT` overlay 后执行。

应用前检查原 `redwood_goodix.c`、`redwood_input.c` 的固定 SHA-256、全部覆盖层源哈希，loader 模式检查精确代码上下文、helper/ABI 哈希及新文件冲突。任何上下文变化、已应用或 include 冲突都在发布前拒绝。JSON 提供 `board_probe_implemented=true`、`photo_probe_boot_arg=chiron_touch_probe=1`、`native_i2c_can_enable_conditionally=true`，状态为 `experimental-retained-handoff-runtime-unqualified`。构建时 `native_i2c_hardware_enabled=false` 表示未执行硬件，不代表运行期永远拒绝；`hardware_touch_available=false` / `hardware_observed=false` 保留未上机验证的边界。

**必须重建 loader 与内核。** 不传触控参数时默认关闭。正常实验用 `chiron_touch=1`；父任务 TOUCH-PROBE 的参数为 `cpus=1 epoch=<unix> -v chiron_touch_probe=1`，没有正常触控参数。已验证 loader selector 接受此完整形式，且 XNU 在读取普通 enable guard 前先解析 probe。若两者都有，probe 仍优先，绝不启动 transport。无需 `touchscreen=1`。

probe 在 `redwood_input_init` 的触控初始化位置同步执行，因此 GUI 尚未启动；panic 文本包括原始 GCC/GPIO/GIC/QUP 快照和有效位图。panic 沿用此固定 XNU 的现有处理，可能经 `loader_return` 返回 resident loader，而不是永久停住或退出固件；显示保留时长、日志实际是否可拍仍需该诊断镜像的实机反馈。本任务没有修改全局 panic/loader-return 策略。

## 验证

```sh
python3 -m unittest discover -s tests -p test_chiron_touch.py -v
clang -target arm64-apple-darwin -std=c11 -ffreestanding \
  -Wall -Wextra -Werror -fsyntax-only drivers/chiron-touch/chiron_dsx.c \
  drivers/chiron-touch/chiron_qup.c drivers/chiron-touch/chiron_f12.c \
  drivers/chiron-touch/chiron_native.c drivers/chiron-touch/chiron_board.c
```

本机测试覆盖：

- 真实固定 HackiOS 源和 fully applied XNU 的相关上下文复制到临时目录后应用；哈希、重复应用、上下文漂移、目标冲突、非 chiron 构建的拒绝。
- 实际 C 核心以 `-Wall -Wextra -Werror -fsanitize=address,undefined` 编译执行；PDT 扫描、未知布局、短读、页选择失败、版本/描述符拒绝、未知对象及长度、全帧原子发布、多指移动与释放、所有槽位、闭区间坐标、布局变化及失败后的释放和停止。
- 原生寄存器模型验证 tag 字节流、计数、pack/unpack、状态顺序、barrier、16/80 字节读取、32/128 字节 FIFO 容量和拒绝缺少交接或单调时钟回调时任何 MMIO；测试 NACK/仲裁/bus/failed/tag/FIFO/超时/短 FIFO 错误不会交付部分数据或自动重试。额外模拟单次等待超时、时钟停滞和时钟倒退，验证截止检查和迭代兜底都能停止。
- 实际 F12 验证器测试制造商/build、F01 状态、Query5/6/8 长度和 presence/subpacket 边界、Ctrl23 数量/映射、Ctrl28 字段、continuous mode、Data0/Data15 与范围；在寄存器模型中执行 native→PDT→F12→Data1→既有事件回调，验证 Data15 对残留对象的释放、前后位图不一致和读帧期间复位的拒绝。
- 板级模型检查严格身份、每个源码 mask 的失败、映射失败、缺少/改变所有权标志、QUP 非 idle/master，以及不同 IRQ 位不影响结果；检查失败时不写任何 MMIO。模型执行真实 probe→native bind→F12→事件路径，检查运行中丢失时钟不再发 QUP 清理写。
- XNU 适配器在主机类型/函数替身下严格编译执行：错误平台在映射前拒绝，默认关闭不启动；直接执行 sleep helper 检查等待语义。probe 模式在错误身份与全部条件满足两种情况下均进入可捕获 panic，无 worker/事件；对所有映射的 GCC/GPIO/GIC/QUP 内存逐字节快照比较，包括非零 QUP_SW_RESET 哨兵，确认没有写寄存器。
- 实际 loader helper 的主机测试覆盖默认关闭、精确 probe argv、正常选择、重复参数、错误 boot CPU（含 Aff3）、PSCI 版本以及每个其它 CPU 的错误状态；仿真只允许 VERSION/AFFINITY_INFO 查询，任何 CPU_ON/OFF 都失败。base-only 和 USB+memory 的完整 AArch64 UEFI freestanding 编译均通过，并核验对象 machine=`0xaa64`。有 `lld-link`（或 `CHIRON_TOUCH_LLD_LINK`）时测试继续链接并校验 EFI PE machine；本机没有该链接器，未在本机声称完成 EFI 链接，由父任务 CI/打包完成。
- 私有原始采集可读时，按 manifest 校验 SHA-256，并直接解析原 runtime DT 的 I2C 基址与 GPIO，而非只匹配文档字符串。
- ARM64 Darwin freestanding 语法检查通过。

本地树没有 `BUILD/` 生成头与匹配的 XNU SDK，本地测试没有完整编译 XNU。
主机适配器编译和独立核心的 ARM64 语法检查不等同完整内核链接；云端结果另记如下。

## 云端构建与探针交付

原生 FIFO/F12 协议阶段运行 `40051773333` 已完成内核与 EFI 链接，
本地核对三个实际产物哈希、ARM64 格式、空未定义符号表与原生协议符号；
该历史版本尚无板级 provider，记录在
`dist/chiron-touch-source-40051773333/local-verification.json`。

当前板级版本源码提交为 `83e31f55553d02fe3df5fa0b846d4eeaba4eaac7`，
运行 `40056688514` 完成全新内核和 loader 链接。所有权未确认时，在读取
QUP_STATE/STATUS 之前拒绝访问，仍可报告已安全读取的 GCC/TLMM/GIC 状态。
本地复核确认原生协议与 `chiron_board_probe/identity/retained` 符号均保留，
内核含实际只读 panic 路径；源码构建产物的三个 SHA-256 与清单匹配。
记录为 `dist/chiron-touch-source-40056688514/local-verification.json`。

上游 make 退出码仍为 2，沿用工程已有的新链接内核和空未定义符号表门槛；
不能宣称全部上游导出检查通过。没有授予 DriverKit/kext 全面兼容性、
触摸可用性或实机运行资格。

运行 `40057611818` 已生成单核的独立 `TOUCH-PROBE` 临时镜像。新手机 loader
按 base→USB→memory→touch 顺序重新编译，传入实际 handoff 属性；payload
严格绑定该新内核，命令行为 `cpus=1 epoch=... -v chiron_touch_probe=1`。
`-v` 在固定 loader 中选择文本 framebuffer；没有 `chiron_touch=1`。
打包必须显式使用源码实验入口，并校验 `board_probe_implemented=true` 和
`probe_only=true`，不继承 HK6c 的桌面反馈。

交付目录为 `dist/chiron-driver-candidates/TOUCH-PROBE-40057611818/`，
包含镜像、manifest、系统卷上传工具和 `START.zh-CN.md`。

| 对象 | SHA-256 |
| --- | --- |
| 26,697,728 字节镜像 | `ed299c08b53d8c39e97130f7a6cae7d36472ecc0a38795efae2c5e3dc7d719e5` |
| 实际内嵌 XNU | `991af46b180c58074d67cb17f7533581877353056558c087327a7ffdbab99920` |

`dist/chiron-touch-probe-40057611818/local-verification.json` 已独立核对镜像
SHA-256、legacy Android SHA-1、FAT 内文件、实际 XNUP 内核/根卷/探针参数、
实验菜单，以及与 HK6c 相同的 UEFI body、附加 DTB 和 USB 入口。
此探针预期在 GUI 前触发诊断 panic，可能返回 resident loader；
照片或返回后的 `oem log` / `get_staged` 日志是下一步接管硬件所需的实际证据。

## 首次探针照片反馈

2026-10-24，用户提供 `IMG_20261024_120203.jpg`。本地原图为 4344×5792，
照片 SHA-256 为 `69720abe1b8e28148efa1b2d9e71c1f000273873840a03977dcd8033a48e47c0`。
可读 kernel UUID 与探针内核的 `05F21AE2-58D1-3BED-918E-66FEDA3034E6`
一致；回溯中的 LR `0xfffffff007494864` 对应前一条
`0xfffffff007494860` 的 `bl _panic`。该指令位于
`redwood_goodix_init+0x19c`，格式串就是 `CHIRON TOUCH PROBE READ ONLY`。
按返回地址减四归属调用点，不能把它误记为下一符号 `chiron_property` 的故障。

此反馈支持已到达预设的同步诊断 panic，不是仅凭通用回溯猜测新增异常。
照片没有包含 panic 开头的 status/bad/obs/owner 和寄存器值，因此尚不能判断
实际时钟、引脚、复位或所有权条件，也不能记为触摸通过或 MMIO 读取成功。
本轮只读 `fastboot devices` 没有枚举到设备，未读取 loader 日志，
未执行手机 boot、flash 或 reboot。原始判断边界见
`research/hackios-reuse/touch-probe-photo-20261024.json`。

## USB 取回实际诊断值

同日用户要求改用 USB 读取。系统 USB 树实际存在 VID/PID `18d1:d00d` 的
`xnuloader`；沙箱内 fastboot 空列表不能作为设备断开的证据。
沙箱外只读确认 product=`xnuloader`、board=`chiron` 后，执行 `oem log` 和
device-to-host 的 `get_staged`，成功保存 21,294 字节日志：
`out/chiron-touch-probe-usb-20261024.log`，SHA-256 为
`2a5cb14e4e50eead7cd5cb20950e60cac42fedf51c81ed80a2c745ba4359559f`。
此次没有 stage、run、boot、flash 或 reboot。

日志确认内核已返回 loader，UUID 与上述探针一致，实际诊断为
`status=-22 bad=1 obs=3f owner=1/1/fe/1`。第一处拒绝来自
APCS_BRANCH_VOTE：实测 `0x80889d80` 缺少 BLSP1 AHB 投票 bit17
（mask `0x00020000`）。AHB_CBCR=`0x8000c220` 的 halt 位为 1；
QUP5_I2C_CBCR=`0x80000000` 的 enable 位为 0、halt 位为 1，
与接口/核心时钟未开启一致。QUP 根命令为 `0x80000000`，配置为 `0`；
BLSP1_BCR=`0`。

这是探针运行时保留的六个 GCC 读数，不是返回固件后重新读取的实时状态。
handoff 元组满足当前实现的执行隔离预检，但不是总线硬件锁或 EBS 证明。
`obs=0x3f` 仅标记前六个 GCC 寄存器；GPIO/GIC/QUP 尚未读取，其零值为占位。
因此不能据此判定引脚或 reset 状态正确/错误，也不能记为触摸可用。
下一步需补齐有来源依据的时钟初始化，再验证 pinmux 与 F01/F12 状态。
结构化记录为 `research/hackios-reuse/touch-probe-usb-20261024.json`。

已据此加入有来源依据的 BLSP1/QUP5 时钟初始化和独立写时钟验证模式，
源码链接运行 `40070403806`、镜像打包运行 `40070898479` 均完成，
静态核对后的交付位于
`dist/chiron-driver-candidates/TOUCH-CLOCK-PROBE-40070898479/`。
此模式会请求有限 GCC 时钟写入，仍不启动触控事务；
旧只读探针保留。初始化顺序、超时和不能保证完成的回滚边界见
`research/CHIRON_HACKIOS_TOUCH_CLOCK.zh-CN.md`。尚无新版本的手机运行结果。

## 2026-10-24 时钟实机反馈与 pinmux 小修

USB 已取回 `out/chiron-touch-clock-probe-usb-20261024-round2.log`（21,811 字节，
SHA-256 `be675804eac027c80a128861ca7bf6e0cb176bcf98fe2a25eb39fc9c2987950e`）。
参数为 `chiron_touch_clock_probe=1`，内核 UUID 为
`B1F4E435-1F0F-3DEC-8C57-029ACEA77883`。时钟准备实机返回
`result=0 step=12 changed=3a`；AHB=`2000c220`、QUP clock=`1`、
root CMD=`0`、CFG=`1`。板级检查前进至 `board=-23 bad=6 obs=3ff`：
GPIO87/88 CFG 均为 `0`，GPIO89 CFG=`203`、IO=`0`。
这是内核运行时快照；GIC/QUP 状态尚未读取，触摸仍未验证。
本轮只执行身份查询及 device-to-host 日志读取，没有启动、上传系统或重启。

小修增加独立 `chiron_touch_pinmux_probe=1`：沿用时钟准备后，仅将
GPIO87/88 mux `[5:2]` 从 GPIO 改为 I2C5 function3，保留 pull/drive；
未知 mux、OE 已启用、时钟或所有权检查失败时拒绝修改。
旧只读/时钟探针保持原行为。它不写 reset、PMIC、IRQ 或 QUP，不创建 worker。
两个 mux 分别读回，不声称原子更新；中途失败可保留已完成的 mux，
`changed` 标记实际尝试的寄存器，后续只读快照用于判断。
本轮目标为 mux 读回 `0xc` 并越过 `bad=6`；按当前快照预计下一阻塞为
`board=-24 bad=9`（reset 仍低）。不以进入桌面或触摸恢复验收。
本地触摸、时钟及打包入口共 48 项检查通过。源码提交
`270fd23a2c98f17fc1cc3b39e97b0c7ebfb68c7d` 的运行 `40072910965`
完成新 XNU/EFI 链接；本地核对产物哈希、ARM64 格式、探针代码及空未定义符号表。
上游 make 的既有导出检查仍返回 2，不声明全部 make 目标通过。
镜像运行 `40074591589` 成功，交付为
`dist/chiron-driver-candidates/TOUCH-PINMUX-PROBE-40074591589/MIX2-EXPERIMENTAL-TOUCH-PINMUX-PROBE.img`，
SHA-256 `8baa6220ddb663dcb0ee3108cafb022b4f623b3e7e0c372ace4db58ca9b43a8f`。
容器校验、FAT 读回、新内核/探针参数绑定及容量检查通过；固件/DTB/USB 入口
与 HK6c 一致，原系统卷复用。记录在
`dist/chiron-touch-pinmux-probe-40074591589/local-verification.json`。
本版 QEMU、GPIO 实机修复和触摸仍未验证，下一步由用户按同目录 `START.zh-CN.md`
启动并取回一次 USB 日志。

### pinmux 实机结果

2026-10-24 已通过 USB 取回
`out/chiron-touch-pinmux-probe-usb-20261024-round3.log`（21,600 字节，
SHA-256 `b8b5730e698a2e6391d4ac762454f5be4394c8b92cd5ab3bdf368ca5c87cb9e9`）。
参数为 `chiron_touch_pinmux_probe=1`，内核 UUID
`E01BC296-D8F6-3A46-97A3-367E2B4B4C67` 与本轮产物一致。
时钟 `result=0 step=12`；pinmux `changed=c0`，GPIO87/88 CFG 均为 `0x0c`，
本轮 mux 假设实机通过。板级拒绝已前进至 `status=-24 failed=9 observed=3ff`：
GPIO89 CFG=`0x203`（GPIO 输出），IO=`0`（输出锁存及输入均低），未满足
mask=`3` / expected=`3`。主动诊断 panic 后已返回 loader。
这是内核运行时保留快照；GIC/QUP/F01/F12 尚未检查，触摸仍未恢复。
本次仅查询身份、导出及读取日志，没有新构建或设备启动/上传/重启。
下一步为核对供电前提与 reset 释放时序，再做单独的最小实验。

### reset 最小实验

本轮只改手机 loader，复用源码运行 `40072910965` 的内核
（SHA-256 `f4807372c55a2b782282edbe84f364bfb9b641bde3f1446e79643bffefeb6950`）。
显式 `--reset-prepare` 在已有 pinmux 探针参数后增加 `chiron_touch_reset=1`；
旧探针不改变。新 loader 经 PMIC VREG 公共协议只读检查 L6/L14：
要求 revision=`0x10005`、enabled/OK 均为 1、分别为 1808000/1880000 µV，
另读 NORTH GPIO50（`0x03932000/+4`），要求 GPIO 输出和高电平。
电压来自固定板级 DT；实测 runtime DT 的 `disp_vddts_vreg` 确认为
GPIO50 active-high、startup-delay=4000 µs。VREG ABI 固定至既有 Qualcomm
参考仓库提交 `20bb8ae36c93fc16bbadda0e0a83f930c0c8a271` 的
`boot_images/QcomPkg/Include/Protocol/EFIPmicVreg.h`，哈希记录在来源清单。
它只证明请求的数字状态，不是外部电压测量；本机尚未确认该协议已安装。

仅在交接/供电及 GPIO89 的现有 GPIO 输出低状态通过时，保持低至少 5 ms，
重查条件，然后只置 `GPIO89_IO` bit1，等待至少 200 ms，再检查 input/output
均高。已高时不重新脉冲。时序来自固定 DSX 驱动及板级 DT，等待同时校验
19.2MHz 计数器实际经过时间。不调用 PMIC 写入、不改 GPIO 配置/IRQ/QUP；
前检失败阻止内核启动并保留 USB 日志。写入后失败保留 changed 状态，
不反复写入或重新拉低 reset。正常成功后仍由原内核执行 pinmux 和板级探针，
目标为越过 `bad=9`，不宣称触摸恢复。
本地 48 项相关检查通过，包括 22 个 reset 情景、ASan/UBSan、实际 loader
ARM64 编译、旧模式隔离及显式打包证明。

云端打包运行 `40076834103` 已完成新 loader 的编译和链接，提交为
`b53fa441a4a6dc3f36b83d4546589c35976a4d92`。交付文件：
`dist/chiron-driver-candidates/TOUCH-RESET-PROBE-40076834103/MIX2-EXPERIMENTAL-TOUCH-RESET-PROBE.img`，
SHA-256 `7ea40c46d0e6812815c2a0b85b07c6982505fa64adeda98209afd2a5c51dfe61`。
本地核对容器 SHA-256/legacy SHA-1、FAT 读回、ARM64 EFI、新 loader helper
哈希/参数/打包提交，以及与原源码构建一致的内核；固件/DTB/USB 入口与 HK6c
相同，容量检查通过。记录为
`dist/chiron-touch-reset-probe-40076834103/local-verification.json`。
这轮未重编内核、不继承新功能运行资格；下一步由用户按同目录 `START.zh-CN.md`
启动，读取供电前检及 reset IO 是否由 0 变为 3。新功能 QEMU/真机仍待验证。

### reset 供电前检实机结果

2026-10-24 USB 日志
`out/chiron-touch-reset-probe-usb-20261024-round4.log` 共 774 字节，
SHA-256 `099568bf658ab53b2f686f3052f9ba8eca048f29911ce2294ac903c1e595c2ab`。
VREG locate 成功，revision=`0x10005`；L6/L14 的 GetLevel/GetStatus 均成功，
enabled/OK 均为 1。L6 返回 `1848000 µV`，与前检要求的 `1808000 µV` 不同，
因此明确拒绝。L14=`1880000 µV`、GPIO50 CFG=`0x3c3` / IO=`3` 均满足现有条件。
这证明固件协议查询已执行；电压值是固件数字读回，不是外部测量，
不能直接认定电源故障或该电压适合触控。未到 GPIO89 读取/写入，
reset untouched，内核未启动，本次没有新的内核 UUID、panic 或触摸运行结果。
下一步核对 L6 读回算法、固件配置与器件允许范围，再决定是否调整判定。
本次仅取回日志并更新状态，没有新构建或设备启动/上传/重启。

### L6 板级电压修正实验

直接解析上述原始 runtime DT：L6 phandle=`0x1e6`，min/max 均为
`1800000 µV`；供电引用只有 Synaptics `@20/vdd-supply` 和未绑定的备用
FocalTech `@38/vddio-supply`。旧前检的 `1808000 µV` 来自参考源码，不能替代
该手机的实测板级配置。本轮不放宽电压比较，独立 `touch-l6-probe` 改为在
reset 仍低时，经 VREG `SetLevelUv(0,5,1800000)` 设置 L6，等待至少 5 ms，
严格读回 `1800000 µV` 且 enabled/OK 后才进入已有 reset 释放流程。
只接受已观察的 `1848000` 启动值或已经匹配的 `1800000`；后者不写电压。
其它电源、模式和使能保持原值，未知 profile、需要改压却 reset 已释放、所有权改变、
写失败/未生效/读回回退均停止，不重试或回写旧电压。
需要编译期开关和 `chiron_touch_l6=1` 同时存在，旧 reset 模式拒绝这个参数。

固定参考源码和本机厂商 `PmicDxe.efi` 的普通 LLVM 静态核对支持该 API 使用
微伏及零基 LDO 编号，没有发现包装层的 40mV 加减。原二进制 SHA-256 为
`16c722765b9862ca28557ef4510c7d8d1a16830e22d8b02f32eb53ad7f3e236c`；
VREG 相符表 RVA=`0x313d0`，GetLevel=`0x21a8`→LDO `0x9ffc`→
电压算法 `0xd7e0`，末端为 RangeMin+VSET×VStep。分析副本只去除 debug 目录引用，
从未用于打包。REA 未连接、CLI 不可用；这些不是 REA Evidence，也不证明
手机执行了具体静态分支或测到了外部电压。来源文件及原二进制哈希已入清单。
本地 50 项相关检查通过（含 42 个寄存器情景、ASan/UBSan、ARM64 编译及旧模式
隔离）。仅需云端重编 loader；原内核和系统输入复用，新功能 QEMU/真机待验证。

云端运行 `40079155452` 已完成新 loader 编译、链接及打包，提交为
`0c777a42b0577f9567f86cc5a1f865f5862aa20c`。交付文件：
`dist/chiron-driver-candidates/TOUCH-L6-PROBE-40079155452/MIX2-EXPERIMENTAL-TOUCH-L6-PROBE.img`，
SHA-256 `42712e60d6295a6bedee14677354b5b89250b1c84e351cc78539dce18b284464`。
容器 SHA-256/legacy SHA-1、FAT 读回、ARM64 EFI、新 helper/参数/写入声明和
打包提交绑定均通过；内核 SHA-256 仍为
`f4807372c55a2b782282edbe84f364bfb9b641bde3f1446e79643bffefeb6950`，
固件/DTB/入口与 HK6c 相同，容量检查通过。
记录为 `dist/chiron-touch-l6-probe-40079155452/local-verification.json`。
按同目录 `START.zh-CN.md` 由用户启动后，读取 L6 `uv=1800000` / `ready=1`
及 reset `io=3`；新功能 QEMU/真机仍未验证。

2026-10-24 USB 读取重试：此前身份查询出现管道错误；重试后 product=`xnuloader`、
board=`chiron` 和日志导出均成功，`getvar result` 为 `idle`。
保存 `out/chiron-touch-l6-probe-usb-20261024-round5.log`（189 字节，
SHA-256 `b400e71493c65d718a457ef66ef0dcfc442726494cd517749d3e587bd85538ba`）。
内容仅有通用 `XNULOADER v3` 启动及 `usb: started`，没有 run、L6/reset 探针或
内核记录；不能据此确认本轮镜像身份、L6 设置或 reset 放行。
本次仅查询和读回日志，没有系统上传或启动；需由用户完成本轮第二阶段命令。

同日后续再次请求读取时，仍可枚举 `xnuloader`，但 `getvar product` 和直接
`oem log` 均无响应，主机报管道错误 `e00002d6`。未发现其它 fastboot/上传进程；
仅结束本次主机查询，没有操作手机启动或重启。未取得新的设备日志，L6/reset
运行结果仍未知，前次 `idle` 不代表此刻状态；需手机当前画面或 USB 恢复后再判断。

用户随后提供完整两阶段终端输出：绝对路径指向 `TOUCH-L6-PROBE-40079155452`，
boot 发送/启动均 OKAY；系统卷发送 2,433,808 KB、用时 74.393 秒并返回 OKAY，
`oem run` 也已应答。不能再将这次状态解释为未执行上传/启动请求。
源码中 `oem run` 先回 OKAY，再调用 `run_kernel()`；L6/reset 前检在实际内核跳转前。
因此输出仅证明请求已收到，尚不能确认 L6 调用返回或 XNU 已执行。
当前没有其它上传/fastboot 进程，USB 无响应的具体阶段仍需手机屏幕最后几行定位。

### L6 停滞照片与屏幕日志修正

用户照片 `IMG_20261024_171449.jpg`（SHA-256
`6477fdfcdc699a38255dd7917e1c144b8512b6b4ea20e6ff2b2d5b9160b7d823`）
显示 TOUCH-L6-PROBE 菜单、19,241,296 字节载荷及 loader 的 `usb: started`。
结合源码确认，USB 命令循环入口会设置 `screen_quiet=1`，后续 `print` 仅写入
内存日志，不能凭这张保留画面定位 L6、reset 或内核阶段。此前请求屏幕末行
不足以解决该观测盲区；照片也不能独立核验镜像哈希或证明 L6 写入发生。

本轮仅在显式 L6 实验的 `oem run` 恢复 console，先清日志，再打印 RUN received、
USB ACK completed、CPU ownership、reset preflight；现有 L6 写调用前增加
calling SetLevelUv 标记。其后沿用既有 set_status、readback、reset 和 jumping
日志，USB 无响应时可由照片确认最后一个已打印阶段。原硬件条件/操作不改，
内核及系统输入复用。原正常模式和其它触摸模式仍按既有静默策略运行。
本地触摸/loader 与 reset 共 29 项检查通过；屏幕可见性及实际停滞点仍待验证。

运行 `40082035584` 已完成新 loader 编译、链接和镜像打包，提交为
`765164630b37904825fb931851e042f2598cdc0b`。交付：
`dist/chiron-driver-candidates/TOUCH-L6-TRACE-40082035584/MIX2-EXPERIMENTAL-TOUCH-L6-TRACE.img`，
SHA-256 `c9f42b29b905846ad9f47844c1800bfcde709657aeffff084cce91fd518c79cf`。
本地容器 SHA-256/legacy SHA-1、FAT 读回、ARM64 EFI、实际屏幕阶段字符串、
helper/参数/打包提交绑定及容量检查通过；原内核、固件/DTB/入口和系统输入复用。
记录为 `dist/chiron-touch-l6-trace-40082035584/local-verification.json`。
按同目录 `START.zh-CN.md` 运行后收集最后一个 TRACE 阶段；真实停滞点及新屏幕
日志仍待实机确认，没有新的 QEMU 或触摸通过结果。

### SetLevelUv 调用区间停滞与只读 ABI 快照

照片 `IMG_20261024_173949.jpg` 的 SHA-256 为
`0c49fea38f10efa3cb2ed8fa57dbb086b70ad46675c3ab8f1298d8b2e31a110c`。
屏幕已显示 RUN/USB ACK completed、内核载荷复制、CPU ownership、VREG locate
和两轮 L6/L14/GPIO50 前检；最后为 `calling SetLevelUv L6=1800000`，
没有随后的 set_status 返回记录。范围已缩小至该调用及紧邻输出路径，
尚未跳入内核；不能仅凭照片断言回调自身永久挂住或电压写入已经发生。
参考二进制的毫伏入口 RVA `0x219c` 只乘 1000 后跳到微伏入口 `0x2118`，
简单换入口仍走相同实现，因此先核对实际安装的协议实例。

本轮独立 `touch-l6-abi` 在 USB 初始化后、命令循环前，仅读取并打印 VREG
revision 和 GetLevel/GetStatus/SetLevel/SetLevelUv 的地址，按原二进制相对 RVA
计算 reference_profile。它不调用这些 PMIC 回调，不读写触控 GPIO；
禁用 oem run/selftest 和 one-shot 内核启动，启动即可取快照，不上传系统卷。
profile 匹配只表示相对布局相符，不是固件字节认证或电源写入资格。
本地 46 项相关检查通过，包含 46 个寄存器/只读快照情景及实际 ARM64 loader
编译；放置到 USB 初始化之后的改动另通过受影响的编译检查。云端交付待完成，
真正的 SetLevelUv 停滞原因仍未确认，没有新的 QEMU/硬件通过结果。

最终运行 `40095714847` 已完成只读 ABI loader 的编译、链接和打包，提交为
`61b5d730456b776ce78c2283dd19f21a00dd51b6`。补充 USB 初始化失败时保持驻留，
避免返回后缓冲区生命周期失效；受影响 ARM64 编译通过。早先运行
`40084463518` 虽构建成功，但缺此修正，不交付。
交付 `dist/chiron-driver-candidates/TOUCH-L6-ABI-40095714847/MIX2-EXPERIMENTAL-TOUCH-L6-ABI.img`，
SHA-256 `0917a532ee9d7f216ddc4e3a8ad7dceb9fe8a7e0abddd7912e5e2c8ca9af4d18`。
容器 SHA-256/legacy SHA-1、FAT 读回、ARM64 EFI、helper/打包提交绑定、
只读声明及容量检查通过；内核、固件、DTB 和入口沿用已核验输入。
记录为 `dist/chiron-touch-l6-abi-40095714847/local-verification.json`。
只需按同目录 START 临时 boot，直接拍下回调地址和 reference_profile，
不上传系统、不请求内核启动。真正的电压调用停滞仍待该快照缩小原因，
本轮没有新的 QEMU 或真机通过结果。

2026-10-24 ABI 实机反馈：照片 `IMG_20261024_221607.jpg`，SHA-256
`f15cbdce88698a53d3dcc23f305977863892819ef9c2f64928151b98dc3ff80b`。
协议地址 `0xffe423d0`、revision=`0x10005`；GetLevel=`0xffe131a8`，
GetStatus=`0xffe132ec`，SetLevel=`0xffe1319c`，SetLevelUv=`0xffe13118`；
reference_profile=`1`、inferred_base=`0xffe11000`，末行 snapshot complete。
四个相对入口及协议表地址与参考布局一致，降低了回调槽位取错的可能性，
不证明固件字节相同、回调内部执行正常或电压已改变。只读快照的本轮硬件
观察点通过；没有系统启动是本版预期，触摸与原调压调用停滞仍未解决。
已有 PMIC 参考二进制的 LLVM 静态路径为
`SetLevelUv 0x2118 -> LDO 0x9fb8 -> 电压算法 0xd5a4 -> SPMI 写接口`，
与先前读电压的路径不同；不能将读成功当作写接口可用的证据。
普通 LLVM 分析不属于 REA Evidence，也不能定位手机实际停滞指令。
下一步聚焦 SPMI 写入及回调返回后的输出边界，保持电压、reset 和所有权前检。
本轮没有修改 loader、重新构建或新增 QEMU 结果。
同轮只读 USB 取日志在等待 `xnuloader` 时 15 秒超时，随后 fastboot devices
列表为空；未取回新日志，也不能据此认定手机内部卡住。没有执行设备启动、
系统上传或重启。

### L6 回调返回与控制台停滞的最小区分实验

2026-10-24 接手时原触摸会话已 idle。只改 loader：供电、reset 和 CPU
所有权前检不变；在 SetLevelUv 前直接写右下角 64×64 红块，回调返回后、
任何格式化日志之前直接改为绿块并清理对应 framebuffer cache。
标记不调用 console、USB 或 PMIC；面板几何不匹配则拒绝调压调用。
绿色证明已越过回调返回边界；红色只能说明未完成绿色标记，不能单独定位
固件内部指令或证明已经写入电压。没有色块时也不能从旧日志推出回调状态。
既有触摸/时钟/reset/打包共 56 项检查通过，含 ARM64 loader 编译、
46 个寄存器情景及 ASan/UBSan；模型检查调用时红块、返回后绿块及写入范围。
隔离提交 `cba1a893a29591b0551b49728c01f42108c3764b` 的云端 loader
编译、链接和打包运行 `40108883864` 成功，使用 `touch-l6-trace`，
复用内核运行 `40072910965`，不重编内核或下载 IPSW。
下载入口 TLS 超时后，按产物编号 `12565299661` 直接取回完成。
交付 `dist/chiron-driver-candidates/TOUCH-L6-TRACE-40108883864/MIX2-EXPERIMENTAL-TOUCH-L6-TRACE.img`，
SHA-256 `a66c63cb5929adf2bda8a536eff5d42fb21bbdc47bbcf1ecf8c689af7d0a6adb`。
容器 SHA-256/legacy SHA-1、ARM64 EFI、FAT 读回、helper/标记/参数/
打包提交及容量绑定通过；内核仍为 `f4807372c55a2b782282edbe84f364bfb9b641bde3f1446e79643bffefeb6950`，
固件/DTB/入口与 HK6c 一致，原系统卷复用。记录为
`dist/chiron-touch-l6-trace-40108883864/local-verification.json`，
准确两阶段命令已写入同候选目录 START。没有新增 QEMU/实机结果，
唯一剩余阻塞为手机上的颜色与末行反馈，触摸仍未验证。

2026-10-25 红绿标记实机反馈：照片 `IMG_20261025_081822.jpg`，
SHA-256 `3d7019e42ed0de87f716c581ce15d58857ab60d4720162cd6d1b220b459c41d8`。
照片可见新 MARKER 日志、右下角红块，末行为
`calling SetLevelUv L6=1800000`，未见绿色或 set_status。
L6=`1848000`、L14=`1880000`、enabled/ok 均为 1，GPIO50 cfg=`3c3`/io=`3`。
直接 framebuffer 标记已可见；尚未完成返回标记，不能据此判定固件内部
具体停滞指令、永久挂死或实际电压写入。未到 reset 释放和内核启动。

下一最小实验只提前同一 L6 前检/调用至 loader 的 USB 会话启动之前，
保留供电、reset 低、CPU 所有权与红绿标记。成功后才开放 USB；
reset 仍在后续 oem run 严格前检后释放。一次启动最多尝试一次 SetLevelUv，
后续调压值回退或首次调用失败均不再次写入。失败保持驻留，不自动启动内核。
目的是判断 loader 的 USB->start_ex/系统上传/内核载荷复制是否为复现的
必要条件，不预先认定 USB 驱动有错。入口 EFI 的 USB 控制器准备保持原样，
因此本轮不隔离整个 USB 栈。

隔离提交 `21396ebc9fa4ed5525bef2965aceab738e8557c6` 的云端运行
`40147754484` 已完成 loader 编译、链接和镜像打包。交付为
`dist/chiron-driver-candidates/TOUCH-L6-TRACE-40147754484/MIX2-EXPERIMENTAL-TOUCH-L6-TRACE.img`，
SHA-256 `08734fe4ab59c1a05fc2ee625bd3bdb3597199b552b37a29b9b5d5d446fc646b`。
本地相关 46 项检查通过，含 ARM64 loader 编译、50 个寄存器情景及
ASan/UBSan；新增情景覆盖早期成功后正常 reset、读回回退拒绝、错误后不重写、
缺少显式参数时无硬件访问。容器 SHA-256/legacy SHA-1、ARM64 EFI、
FAT 读回、helper/提前调用声明/阶段字符串/参数/打包提交及容量绑定通过。
内核仍为 `f4807372c55a2b782282edbe84f364bfb9b641bde3f1446e79643bffefeb6950`，
固件/DTB/入口与 HK6c 一致，系统输入复用。静态记录：
`dist/chiron-touch-l6-trace-40147754484/local-verification.json`。
先只临时 boot 并拍早期末行与色块，无需先传系统；只有 EARLY ready=1
及 usb: started 后才按同目录 START 继续原上传流程。新版本 QEMU/实机
尚未验证，唯一剩余阻塞为这次早期调用的手机反馈，触摸仍未恢复。

2026-10-25 早期调用反馈：照片 `IMG_20261025_085548.jpg`，SHA-256
`b1a54d75f13f80a835dab3dbbcdd86dc4450182193f6bdc631db3ca8dba7af6a`。
可见 EARLY before USB、VREG revision=`10005`，供电读回与前轮相同；
末行为 calling SetLevelUv，右下角仍红，未见绿色、set_status 或 ready。
loader USB->start_ex、系统上传和内核复制不是复现所必需；入口 EFI 已有
USB 控制器准备并未排除。未观察到调压返回、reset 释放或内核启动。

只核对已有参考 SPMI 的相关包装层，原二进制 SHA-256
`910bb5fc610ba2a56b640827527e8ce51d5b47a6501cad371eba3b3585e2a868`。
普通 LLVM 显示读包装 RVA `1440` 与写包装 `14f8` 均调用
BootServices RaiseTPL(8)，工作返回后 RestoreTPL；写工作跳至 `2f28`。
这是参考二进制的静态代码，非 REA Evidence，不证明实机停滞在 RestoreTPL。
REA 未连接，本轮不安装工具或扩大逆向范围。

下一实验只将电压调用包在外层 TPL_CALLBACK(8)：先用 RaiseTPL(31)/
RestoreTPL 原值取当前 TPL，只接受 4 或 8，再重查原供电/GPIO/所有权条件。
绝不在 TPL_HIGH_LEVEL 调 PMIC。调用前红、API 返回后绿，恢复外层 TPL
返回后蓝；在恢复 TPL 前不调用格式化日志，错误返回也恢复原 TPL。
TPL 服务缺失或原级别不符时不调压；单次写入、原目标和 reset 条件保持。
该实验改变事件分发时序，绿不等于原调用路径正常，蓝不等于电压读回成功；
更高 TPL 通知也未被全部隔离。

云端运行 `40150211449` 已完成新 loader 编译、链接和打包，提交为
`488ef98206a05e1b55e5bc1d2f3afbd09cca6a9a`。交付：
`dist/chiron-driver-candidates/TOUCH-L6-TRACE-40150211449/MIX2-EXPERIMENTAL-TOUCH-L6-TRACE.img`，
SHA-256 `76843ca03a4d2d0b583bf094de387e1d288813f386f753c1a89f92f096bd27f3`。
本地相关 46 项检查通过，含 ARM64 loader 编译、54 个寄存器/TPL 情景、
ASan/UBSan；模型确认写 API 在 TPL=8 执行、绿色先于外层恢复、蓝色随后，
错误返回也恢复原级别；缺 TPL 服务和未知级别时无调压写入。
容器 SHA-256/legacy SHA-1、ARM64 EFI、FAT 读回、helper/提前调用/
TPL 范围声明/新阶段字符串/参数/提交及容量绑定通过。内核仍为
`f4807372c55a2b782282edbe84f364bfb9b641bde3f1446e79643bffefeb6950`，
固件/DTB/入口与 HK6c 一致，系统输入复用。静态记录为
`dist/chiron-touch-l6-trace-40150211449/local-verification.json`。
只先临时 boot 并拍当前 TPL、末行和红/绿/蓝块；准确命令在同目录 START，
无需先传系统。新版本没有 QEMU 或实机结果；唯一剩余阻塞为手机上的
TPL/返回标记反馈，触摸仍未恢复。

### 2026-10-25 USB 调试闭环与本地清理

照片 `IMG_20261025_100303.jpg`，SHA-256
`88cb6d06ace1bddea1317aa00470657add08c9e477cf295a83417edf3a8e32b5`，
显示 current TPL=4、outer=8，末行为 calling SetLevelUv，右下角仍红。
外层 TPL_CALLBACK 实验未观察到 API 返回；不能定位固件内部指令或确认实际写压。
当前 L6 TRACE 版本在 USB 初始化之前调用，故此卡点没有可服务的 USB 日志通道。

用户本轮明确授权代理执行第一阶段 ADB reboot bootloader、fastboot boot、
USB 取日志及调试重启，取代此前这些操作仅由用户执行的会话限制。
未执行分区 flash、系统卷上传或 oem run。
已实际完成：Android `65c68636` → adb reboot bootloader → 原机 fastboot →
临时启动已核验的 `TOUCH-L6-ABI-40095714847` → xnuloader oem log/get_staged →
xnuloader reboot → Android ADB 恢复且 sys.boot_completed=1 →
adb reboot bootloader → 原机 fastboot。当前设备停在原机 fastboot。
日志为 `out/chiron-touch-l6-abi-usb-20261025.log`，SHA-256
`cfc2d913b604d6f380847d79adc55d62d101dab739e0e07739ed7093d225808f`；
reference_profile=1、snapshot complete。该只读镜像没有启动内核或触控。
源码中的 reboot-bootloader 也仅匹配普通冷重启分支，不能据此声称支持直接
返回原机 fastboot；经 Android/ADB 中转的闭环已在本机通过。
该结果仅适用于 USB 仍可响应的路径，不证明 PMIC 卡住时也能远程恢复。

按用户要求清除 57 个已淘汰的调试镜像及大体积中间文件，逻辑大小
1,627,150,368 字节；删除前后可用空间增加 1,627,181,056 字节（约 1.52 GiB）。
清单为 `out/failed-image-cleanup-20261025.json`。保留 HK6c 桌面基线、
系统卷、当前 L6 TRACE、只读 ABI 镜像，以及源码、清单、日志和截图。
旧交付记录中部分镜像路径因此仅供历史追踪，文件已按本次清单删除。
本轮无新源码构建或 QEMU 资格；触摸仍未恢复。下一实验必须先解决 PMIC
阻塞期间的 USB 可观测性与恢复边界，不能将正常路径的重启闭环当作已解决。

### L6 计算与 SPMI 事务边界截取

本轮独立 `touch-l6-capture` 复用内核与固件，只重建 loader。
USB 先启动，禁用内核启动；保留 L6/L14/GPIO50、reset 低及 CPU 所有权前提。
核对原 VREG 相对入口、固定 PmicDxe WriteLong 包装指令和可写 RAM 范围，
要求其私有 SPMI 缓存指针等于当前 LocateProtocol 实例。
仅在这些条件匹配后，在 TPL_NOTIFY 暂时将该私有指针指向 loader 的影子协议；
所有六个影子入口均拒绝，绝不转发硬件请求。原 SetLevelUv 只运行计算，
截取第一个 SPMI 请求的类型、slave、地址及最多四字节数据，然后恢复原指针
和 TPL，重新检查供电与 reset 未变。前后仍会执行既有只读供电查询。
这不是一次成功调压；它区分“到达底层事务前阻塞”和“进入 SPMI 原实现才阻塞”。
普通 LLVM 静态代码用于选择固定包装指令，不是 REA Evidence；本轮构建与
实机结果待追加，不能继承旧镜像的硬件通过资格。
