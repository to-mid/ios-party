# MIX 2 / chiron 有界 SMP 实验补丁

本文件记录 2026-10-23 的源码实现与宿主验证范围。它不是多核实机认证；
本任务没有执行手机 boot、flash、reboot，也没有执行 QEMU。
用户观察到的可进桌面版本仍是原单核版本。默认 profile 不变。

## 接口与集成顺序

`ci/hackios/apply_chiron_smp.py` 提供三个接口，返回值可以直接 JSON 序列化：

```python
loader_overlay(source_repo: Path, cpu_count: int) -> dict
kernel_overlay(xnu_tree: Path) -> dict
command_line(cpu_count: int) -> str
```

`source_repo` 指向包含 `xnuloader/loader.c` 的 HackiOS `kernel/` 目录，
不是本仓库；`xnu_tree` 指向已应用 HackiOS 和 CHIRON_PORT 补丁的 XNU 根目录。
内核接口只接受源码路径，不读取 variant、核数或其他环境变量。
它从 loader 的 `/chosen/chiron-smp-cpu-count` 读取运行时选择。

主任务应按以下顺序集成，每次使用新的 scratch 源码：

1. loader：`apply_chiron.loader_overlay`，然后原有 USB / 内存 overlay，
   最后 `loader_overlay(repo, 2/4/8)`。无 USB、只有基础 chiron overlay 的
   loader 也支持。SMP 必须在 USB overlay 后应用，因为 USB 会改写单核 guard。
2. kernel：HackiOS XNU patches，然后 `apply_chiron.kernel_overlay`，
   再 `kernel_overlay(tree)`。其他独立驱动补丁由父任务决定组合顺序。
3. 从这些源码重新编译内核与 loader，分别记录返回的文件哈希和构建产物哈希。
   **所有多核实验都必须重建内核**；原来已进桌面的内核不能复用。
4. 构造实验 payload 时将现有 `cpus=1` 替换为 `command_line(count)`，
   即 `cpus=2`、`cpus=4`、`cpus=8`。不要把新参数追加到旧 `cpus=1` 后面：
   重复 CPU 参数会被拒绝。普通单核 payload 仍可在实验 loader 上运行。
5. 无 variant / baseline 路径不调用 SMP overlay，保持原 profile 和原构建行为。
   `smp2/smp4/smp8` 只表示编译选择，不得继承原单核桌面认证。

命令行入口也可单独使用：

```sh
python3 ci/hackios/apply_chiron_smp.py loader \
  --source "$WORK/kernel" --cpu-count 2 --report "$EVIDENCE/smp-loader.json"
python3 ci/hackios/apply_chiron_smp.py kernel \
  --source "$XNU_TREE" --report "$EVIDENCE/smp-kernel.json"
```

报告字段 `hardware_observed` 始终为 `false`，`kernel_rebuild_required` 对多核
loader 和所有 kernel overlay 报告为 `true`。补丁重复应用、上下文变化、已有
不同内容的 SMP include 都报错。所有替换上下文先在内存中检查完，再写 scratch 树；
这不是跨文件的文件系统事务，I/O 失败后应丢弃 scratch 树重新构建。

## 为什么不是 cpus=8 一处修改

核对的本地源码：

| 文件 | 实际路径 / 作用 |
| --- | --- |
| HackiOS loader | `/private/tmp/chiron-hackios-reuse/kernel/xnuloader/loader.c` |
| 完整 XNU | `/private/tmp/chiron-port-xnu`，已应用 CHIRON_PORT |
| PSCI | `osfmk/arm64/psci_smp.c`，按 `cpu_phys_id` 调 CPU_ON，并等待 scheduler running stage |
| reset vector | `osfmk/arm64/start.s`，GENERIC_PSCI_SMP 路径保留 Aff0+Aff1 并遍历 CpuDataEntries |
| GIC | `osfmk/arm64/gic_v3_irq.c`，按 GICR_TYPER affinity 找 redistributor，用 Aff1 和 Aff0 构造 SGI |
| CPU 注册 | `iokit/Kernel/GenericARMPE.cpp`，先注册所有 CPU，再依次调用 processor_start |
| XNU 拓扑 | `osfmk/arm64/machine_routines.c` 与 `pexpert/pexpert/arm64/board_config.h` |

原 loader 的 `core << core_shift` 无法表达 profile 中的实测 MPIDR：

```text
逻辑 ID      0  1  2  3    4    5    6    7
MPIDR        0  1  2  3  256  257  258  259
Aff1         0  0  0  0    1    1    1    1
Aff0         0  1  2  3    0    1    2    3
```

本补丁固定使用该表的前缀；2/4 核只使用第一个 cluster，8 核包含两簇。
不重排到大核启动，不接受 bootcpu 或任意 cpumask。
不添加 `small-core`、P/E 调度类型或猜测缓存大小，沿用通用 SMP 调度。
频率管理、热管理、系统睡眠和 CPU 热插拔不属于此实现范围。

原 BCM2837 配置只有一个 cluster。原 `HAS_CLUSTER=0` 拓扑分支还会把所有
SMP CPU 合并到一个逻辑 cluster，并只初始化 `cluster_offsets[0]`；
单改 loader 会遗漏第二簇的数组容量和 offset。

新 `CHIRON_SMP` 宏仅在该实验内核且已有 `CHIRON_PORT` 时定义；
BCM2837 的 cluster 容量变成 2。只把 `machine_routines.c` 的五处拓扑 bookkeeping
分支扩展为 `HAS_CLUSTER || defined(CHIRON_SMP)`：cluster 初始化标记、
物理 cluster 解析、每簇最大 Aff0、cluster offset 构造、cluster master 注册。
**不定义 HAS_CLUSTER**，避免 `start.s` 与 `machine_routines_asm.s` 顺带访问
Apple 专用 CPU_OVRD 寄存器。PSCI 和 GIC 的通用核心路径继续复用。

## 启动前的限制与拒绝条件

loader 构建上限只允许 1/2/4/8，运行时也只允许这些核数且不能大于构建上限。
无 `cpus` 参数仍选单核。只接受实测启动 affinity 0，并保留高 affinity 位以防
错误掩码把未知 CPU 误识别为 0。

在多核请求时执行只读预检，不在 loader 中执行 CPU_ON：

- 内核加载范围内必须包含 SMP ABI v1 的两个相邻 64 位常量。
  内核 volatile ABI 数据被启动验证引用，防止编译器把它仅优化为指令立即数。
  此标记是兼容性门槛，不是签名或来源认证；来源仍靠父任务构建清单的 SHA-256。
- 平台必须使用 SMC、实测 GICD `0x17a00000`、GICR `0x17b00000`、timer INTID 19。
- PSCI_VERSION 必须报告 0.2 或 1.x。未知 major、负错误值均拒绝。
- 每个次级核 AFFINITY_INFO 必须明确为 OFF=1；ON、ON_PENDING、NOT_SUPPORTED、
  其他错误均拒绝，不“跳过”忙核后悄悄减少数量。
- 在 1 MiB、`0x20000` 步长的 GICR 区域内匹配每个选定 CPU 的完整 affinity。
  检查 LAST、重复 affinity 和不支持的 VLPIS 帧格式；不依赖帧顺序等于 CPU 顺序。
- 拒绝重复或裸 `cpus`、非法数字、超限核数、bootcpu、cpumask 和控制空白。
  ADT 发出前再解析最终 boot args，防止截断改变 CPU 选择。

内核 `psci_smp_init` 校验 ADT 核数、启动逻辑 CPU、SMC conduit 和每项 MPIDR、
Aff0/Aff1 属性；CPU_ON 前再次确认目标位于选定表内且当前为 OFF。
固件拒绝 CPU_ON 或原有 5 秒启动等待失败会 panic，保留现有 stage / early fault
诊断，避免显示为成功的降级启动。原有 cache clean、停止次级核和返回 loader
链路继续使用。GIC SGI 添加低 16 位 affinity / Aff0<16 的边界检查，
GICR wake 等待增加基于 CNTVCT/CNTFRQ 的 1 秒超时。

检查 OFF 与 CPU_ON 之间仍可能被固件改变状态，不能把一次检查当成永久 CPU
所有权凭证；CPU_ON 错误会被显式报告。GICR 的可读性、SMC 是否会陷入异常、
跨簇 cache coherency、TLB shootdown、IPI、实际 scheduler 并发、panic 返回后
CPU_OFF 和后续再次启动都仍需运行时证据。已有通用 GIC distributor 等待路径
不是本补丁的全面重写；这里的“有界”主要指固定拓扑、核数、接口和受支持条件。

## 可复现验证

```sh
python3 -m unittest discover -s tests -p test_chiron_smp.py -v
```

2026-10-23 本机执行结果：8 项测试全部通过，无 skip，耗时 21.789 秒；
其中完整 loader 目标文件编译包含 12 个组合。

测试包含独立于外部源码目录的宿主 C 执行：对四种构建上限运行真实 loader include
的参数解析、ABI 检查、PSCI 错误处理和 GICR 遍历；仅替换 PSCI 与 MMIO 读取。
另以 DT / topology doubles 执行真实内核 include 的拓扑验证与 CPU_ON 前检查。
覆盖不足、重复和乱序 GICR、第二簇 busy 核、PSCI 版本、非法 affinity、重复参数、
输出数组边界、核数不匹配与 CPU_ON 越界。预检 doubles 拒绝任何 CPU_ON 调用。

固定源码存在时，测试在临时副本应用真实基础、USB、内存和 SMP overlay，
对三种组合乘四种核数进行完整 loader 的 AArch64 COFF **目标文件编译**，
不是只有提取函数的语法检查。命令使用：

```text
/usr/local/opt/llvm/bin/clang --target=aarch64-unknown-windows \
  -ffreestanding -fno-builtin -fno-stack-protector -fshort-wchar \
  -mgeneral-regs-only -O1 -Wall -Wextra -Wno-unused-parameter -Werror \
  -c loader.c -o loader.obj
```

另外对真实 board_config 及依赖预处理，分别检查有/无 CHIRON_PORT 的容量，
验证 CHIRON_SMP 定义顺序且 HAS_CLUSTER 保持关闭。
提取并执行打补丁后的真实 XNU 拓扑代码，验证 1/2/4/8 核下的 cluster ID、
Aff0/Aff1、offset、CPU mask 和 PPL 数组索引；8 核应得到 offset `[0,4]`。
同样执行真实 GIC `gic_send_sgi` 函数，仅捕获 sysreg 写入，检查两簇全部核的
SGI 0/1 target list 与 Aff1，并验证不支持的 affinity / SGI ID 会拒绝。
另验证晚期 kernel 替换上下文失效时不会提前发布其他文件修改。
没有完整源码时，这部分明确 skip，不能声称已验证 loader 编译。
环境变量 `CHIRON_SMP_TEST_SOURCE` 和 `CHIRON_SMP_TEST_XNU` 可指定其他固定快照。

本机没有完整 XNU 构建所需的 iPhoneOS16.5.xnu SDK / BUILD 产物，本任务未完整
编译或链接 XNU，也未链接 loader.efi。Linux Actions 必须执行完整内核编译、
loader 链接和 undefined-symbol 检查；之后再做独立实验运行时验证。
宿主测试通过与目标文件编译通过都不代表第二核已运行，更不代表八核桌面通过。

SMP 实现文件为 `apply_chiron_smp.py`、`chiron_smp_loader.inc`、
`chiron_smp_kernel.inc`、`tests/test_chiron_smp.py` 和本文件。
共享构建接线由父任务单独集成，原 profile 和既有认证结果保持不变。

## 并行源码构建入口

`ci/hackios/driver_variants.py` 将实验分为 `touch`、`smp2`、`smp4` 和 `smp8`；
未设置 `HACKIOS_CHIRON_VARIANT` 时仍为 baseline。实验禁止通过两个旧 kernel
reuse 环境变量复用内核；`build-chiron-kernel.sh` 在 CHIRON_PORT overlay 后应用
对应 kernel overlay，并在编译后生成绑定最终产物哈希的 `driver-build.json`。

手动工作流 `.github/workflows/hackios-chiron-drivers.yml` 提供独立的
`touch`、`smp2`、`smp8` 三个并行 job，单项失败不取消其他项。
它只准备固定源码并重新编译内核和 loader，不下载或重建 Apple 系统卷，
不生成手机 boot 容器，也不执行 QEMU 或手机操作。
工作流源码已在本地完成 YAML 解析检查，尚未推送或运行云构建。

构建报告明确保留 `runtime_qualified=false`、`hardware_observed=false` 和
`phone_boot_ready=false`。原 `chiron.py` 的 images、qemu、summarize 入口拒绝
实验 variant，避免把旧单核桌面资格用于新源码。生成可测手机镜像仍需要完整
构建通过、替换 payload 中的 CPU 参数、匹配新版 loader/内核并独立进行运行验证。
