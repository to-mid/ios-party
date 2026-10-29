# MIX 2 / chiron 有界 SMP 实验补丁

## 当前状态（2026-10-29 汇总）

最新八核触摸组合为镜像构建 `40457536115`，已随
`chiron-touch-smp-20261027` 发布，单核、双核包独立保留。
实际 QEMU 运行 `40457857698` 的八核双簇、SpringBoard 桌面及 540 秒定时返回
经保存证据复核 `40459663351` 通过，未重复执行内核。
2026-10-27 实测确认最新八核可用；尚未补充该次真机日志及运行镜像哈希复核，
不扩展为长期稳定资格。详细修复与证据按时间顺序保留在文末；
以下初始单核限制和待验证状态只描述对应历史候选。

## 初始实现记录（2026-10-23）

本文件记录 2026-10-23 的源码实现与宿主验证范围。它不是多核实机认证；
本轮没有执行手机 boot、flash、reboot，也没有执行 QEMU。
当时实测可进桌面的仍是原单核版本。默认 profile 不变。

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

集成时按以下顺序集成，每次使用新的 scratch 源码：

1. loader：`apply_chiron.loader_overlay`，然后原有 USB / 内存 overlay，
   最后 `loader_overlay(repo, 2/4/8)`。无 USB、只有基础 chiron overlay 的
   loader 也支持。SMP 必须在 USB overlay 后应用，因为 USB 会改写单核 guard。
2. kernel：HackiOS XNU patches，然后 `apply_chiron.kernel_overlay`，
   再 `kernel_overlay(tree)`。其他独立驱动补丁按集成配置决定组合顺序。
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
  此标记是兼容性门槛，不是签名或来源认证；来源仍靠云端构建清单的 SHA-256。
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

本机没有完整 XNU 构建所需的 iPhoneOS16.5.xnu SDK / BUILD 产物，本轮未完整
编译或链接 XNU，也未链接 loader.efi。Linux Actions 必须执行完整内核编译、
loader 链接和 undefined-symbol 检查；之后再做独立实验运行时验证。
宿主测试通过与目标文件编译通过都不代表第二核已运行，更不代表八核桌面通过。

SMP 实现文件为 `apply_chiron_smp.py`、`chiron_smp_loader.inc`、
`chiron_smp_kernel.inc`、`tests/test_chiron_smp.py` 和本文件。
共享构建接线单独集成，原 profile 和既有认证结果保持不变。

## 并行源码构建入口

`ci/hackios/driver_variants.py` 将实验分为 `touch`、`smp2`、`smp4` 和 `smp8`；
未设置 `HACKIOS_CHIRON_VARIANT` 时仍为 baseline。实验禁止通过两个旧 kernel
reuse 环境变量复用内核；`build-chiron-kernel.sh` 在 CHIRON_PORT overlay 后应用
对应 kernel overlay，并在编译后生成绑定最终产物哈希的 `driver-build.json`。

工作流 `.github/workflows/hackios-chiron-drivers.yml` 提供独立的
`touch`、`smp2`、`smp8` 三个 job，单项失败不取消其他项。手动输入可以选择
全部、仅 SMP 或仅触摸；实验分支上的工作流文件变更会触发两个 SMP job。
它只准备固定源码并重新编译内核和 loader，不下载或重建 Apple 系统卷，
不生成手机 boot 容器，也不执行 QEMU 或手机操作。
工作流已完成本地 YAML 解析检查并发布到独立分支
`codex/chiron-parallel-drivers`，API 发布后核对远端 Git tree 与本地提交一致。
首轮运行 `39963940121` 停在宿主测试编译：GCC 对测试缓冲区的 `snprintf`
报截断告警，XNU 编译尚未开始。缓冲区从 16 字节改为 32 字节，并修正旧版
cloud 脚本不支持 diagnostics 子命令的问题；本地相关三项测试复跑通过。
修正后的远端源码提交为 `e25128d309de6295741d7c1368d6379601ea4014`；
运行 `39964860075` 的 `smp2` 与 `smp8` 两项均通过完整构建，包括重新应用
固定 XNU 源码、SMP overlay、依赖准备、实际内核链接、loader EFI 链接与
未定义符号检查。两项均没有设置旧内核 reuse 环境变量。对应产物为
`chiron-driver-source-smp2-39964860075`、
`chiron-driver-source-smp8-39964860075`。两份产物已下载到
`dist/chiron-driver-build-39964860075/`，本地独立核对清单与二进制 SHA-256、
ARM64 Mach-O / EFI 格式、SMP ABI 数据和空未定义符号表，结果为该目录下的
`local-verification.json`。

上游 make 实际退出码均为 2，不能声称全部 make 目标通过。完整日志
`out/chiron-driver-cloud-39964860075.log` 确认错误来自既有 `check_all_exports`
和 IOKit / Libkern / Private symbolset 的导出名单检查；内核已先完成
LD、DSYMUTIL、STRIP，并打印 built kernel。实验构建沿用原项目的门槛：
要求本轮新链接产物、空未定义符号表和实际产物哈希；本轮日志未见源码编译错误。
此次没有修复上游 SDK 导出名单，不能据此承诺完整 DriverKit / kext 兼容性。
此运行没有执行 QEMU 或手机测试，不能将完整编译通过记为多核运行通过。

构建报告明确保留 `runtime_qualified=false`、`hardware_observed=false` 和
`phone_boot_ready=false`。原 `chiron.py` 的 images、qemu、summarize 入口拒绝
实验 variant，避免把旧单核桌面资格用于新源码。源码实验镜像使用下述独立路径，
绑定新内核、loader 与 CPU 参数，运行资格仍需独立验证。

## 独立临时镜像

运行 `40053115918` 已从上述真实源码产物分别生成双核、八核镜像。
`driver_phone.py` 先校验源构建编号、实际内核哈希、SMP ABI 和空未定义符号表，
再按 base→USB→memory→SMP 顺序编译手机 loader，生成单一 `cpus=2/8` 参数的
XNUP。打包器必须显式使用 `--source-experiment`，逐项读取实际 XNUP 的内核、
小根卷、USB 模式与命令行，拒绝继承旧桌面资格。普通基线打包门槛保持不变。

两份镜像均为 26,697,728 字节，菜单明确显示 `EXPERIMENTAL SMP2/8`。
交付目录：

- `dist/chiron-driver-candidates/SMP2-40053115918/`，含双核镜像、manifest、
  上传工具与 `START.zh-CN.md`。
- `dist/chiron-driver-candidates/SMP8-40053115918/`，含独立八核实验与对应说明。

| 候选 | 镜像 SHA-256 |
| --- | --- |
| SMP2 | `b1697b7f74633f39084754fe8523e24f26320f08279d166896930606337faedc` |
| SMP8 | `dfedd0eaebabd5df1fc020be71c531a0cd4351136a65c703c94c9e9f9be4ef46` |

本地静态复核 `dist/chiron-smp-candidates-40053115918/local-verification.json`
验证 Android boot SHA-1、整镜像 SHA-256、FAT 中的入口/loader/XNUP，
并将 payload 内核与此前独立校验的源内核逐字节哈希绑定。
实际 UEFI body、附加 DTB 和 USB 入口与 HK6c 一致；仍复用同一原系统卷，
上传工具会再次计算实际大卷 SHA-256。

优先收集双核启动反馈，再测八核。两份镜像没有可用触摸，
没有实机在线核数或稳定性结果；固件和静态校验不能代替这些观察。

## 与单核触摸里程碑组合

新源变体 `touch-smp2` 与打包选项 `touch-smp2-qos` 保留既有 MPIDR/PSCI/GIC 检查，
并接入 DSX、HOME 和已发布的 QoS 用户态。所有固件 AP 仍必须在直接交接前 OFF；
由 XNU 后续启动 CPU1，返回固件前须确认 CPU1 OFF，失败留在内核。
该严格返回门禁只对组合构建启用，旧独立 SMP 变体保持原行为。
用户态从 `chiron-touch-20261026` 固定镜像哈希提取复用，单核基线及发布标签保持。
新组合的宿主加载器编译/返回门禁/资格检查已通过；完整云端构建与实机资格待取得。

源构建 40433616294、打包 40434498916 已完成。新组合候选位于
`dist/chiron-driver-candidates/TOUCH-SMP2-QOS-40434498916/`。
内核 f666f7da 已链接、未定义符号为空，保留总 make=2 记录；
镜像 c257d019 已通过容器/FAT/EFI/来源/容量绑定，实际 cpus=2 且物理触摸和 QoS 开启。
用户态固定复用单核发布小卷 f08dd1fe；无新增 QEMU/手机执行，
实际第二核在线、触摸、性能与严格返回仍需本轮硬件反馈。

## 八核触摸组合

按计划新增 `touch-smp8` 源变体及 `touch-smp8-qos` 打包选项，
沿用同一 MAX_CPUS=8/双簇 SMP 实现和严格固件返回门禁，不改变 ABI、寄存器策略或内存窗口。
重新编译并独立绑定八核源记录；QoS/HOME 小卷、固件和大系统卷仍固定复用。
参数/来源/组合用例扩展覆盖 2 与 8；35 项相关检查通过，
固定源码集成类因本地缓存缺失跳过；工作流 YAML/shell 语法通过。
源/加载器编译及八核实际在线、触摸、返回、性能仍待取得，不继承旧独立 SMP8 或双核资格。

八核源构建 `40438258735` 和打包 `40438983828` 已成功，提交
`c4b26069bfc76b20c4777ec8497d2fbc56b2d9fa`。
交付 `dist/chiron-driver-candidates/TOUCH-SMP8-QOS-40438983828/MIX2-EXPERIMENTAL-TOUCH-SMP8-QOS.img`，
SHA-256 `9483a0a6265c3f638549235ae64407771c78f95475498db3150fb714e2647bdb`。
内核 SHA-256 `65900a6f5937810df0e6251440119b2fd8ea99558cd981ff135b5625d7e5111f`，
链接完成且未定义符号为空，保留总 make=2 记录。
云端源/打包及本地容器/FAT/EFI/来源/八核参数/容量验证通过。
固件、DTB、入口、QoS/HOME 小卷及原系统卷保持，profile 核数为 8。
START 给出独立八核命令和 `out/chiron-touch-smp8-qos-40438983828-run1.log` 取证路径；
无新增 QEMU/手机操作，全部八核实机资格仍待本轮硬件反馈。

### 2026-10-27 八核组合首轮实机日志

只读 `fastboot get_staged` 已取回
`out/chiron-touch-smp8-qos-40438983828-run1.log`，SHA-256 为
`ae9f037eee8f4593385c193289816740a2a8358b2636b7c4b395d0448512d0c4`。
loader selected 8、内核 ABI validated 8 均出现，CPU0–CPU7 完成注册；
实际启动记录只到 CPU1，并出现 `arm_cpu_init(): cpu 1 online`。
CPU0 随后报告 `Ticket lock ... is unexpectedly owned by thread ... @lock_ticket.c:143`；
日志没有 CPU2–CPU7 的 `cpu_start`，不能记为八核上线，也没有本轮桌面/触摸资格。

使用源构建 40438258735 的符号表解析，锁地址 `0xfffffff007ac8880`
对应 `pset0+0x100`，调用链为
`psci_smp_start_cpu+0x2d0 → delay_for_interval+0x8c → thread_block_reason →
thread_select → ticket lock panic`。源码确认 CPU_ON 后的等待调用了可进入
`thread_block` 的 `delay_for_interval(1, NSEC_PER_MSEC)`；这是下一轮应聚焦的
启动等待/调度路径，尚不能仅据栈证明锁重入或所有权异常的根因。
`cpu_signal failed in DebuggerXCallEnter` 出现在 panic 的调试器收核阶段，
不能单独当作最初的 CPU_ON 失败。日志确认内核已返回 loader，
但未提供其余核逐项 OFF 的独立观测。本轮仅取证和源码/符号核对，
没有源码修复、新构建、新 QEMU 或设备启动/上传/重启操作。

双核随后经实测确认运行正常；这是现场观察记录，尚未补充该轮日志或核对
所用镜像哈希。继续核对本轮八核的源码和实际未剥离内核，发现明确的锁约定缺陷：
`sched_steal_thread_enabled` 在同一 node 有多个 pset 时返回 true；
`sched_clutch_steal_thread` 却是仅返回 NULL 的单簇占位实现，没有释放 pset 锁。
`thread_select` 要求此回调返回时已解锁，并会再次调用 `pset_lock`。
八核双簇注册触发该分支，双核单簇不触发，因而无需等第二簇 CPU_ON
就可能 panic。实际内核该回调只有 `mov x0, #0; ret`，
回溯地址 `thread_select+0x920` 的前一条指令正是 `lck_ticket_lock`。
该缺陷与本轮重复持有 `pset0` 的 panic 及双核对照吻合；
下一轮应先修复该回调的锁约定，不将 `delay_for_interval` 本身当作已确认根因，
也不以添加大小核标签替代此修复。尚未修改 overlay 或生成修复候选，
修复后的八核运行仍待独立验证。

### 双簇空回调解锁修复

`apply_chiron_smp.py` 现对真实 `sched_clutch_steal_thread` 添加仅限 CHIRON_SMP
的 `pset_unlock(pset)`，满足 `thread_select` 的返回约定。未改启动等待、
CPU 类型、PSCI、GIC、ABI 或严格收核门禁，也未引入跨簇取任务算法。
定向回归提取真实原版/补丁回调执行：原版双簇检测到锁未释放，
补丁 SMP 版本允许调用者重新加锁；单簇不调用回调，非 SMP 保持原实现。
本地 11 项 SMP 检查无跳过（含真实 loader ARM64 目标文件编译），
7 项变体和 25 项手机载荷检查及 Python 语法通过。
第一次本地尝试误用已打补丁的 loader 快照而失败；取回固定提交
8e611870 的三个干净 loader 源文件后，完整 SMP 检查通过。

提交 `d6f769271ed5673ad0d93bac31833e8db0c50353` 的八核触摸源构建
`40442787624` 成功，内核 SHA-256 为
`4139b5db0e15c4f60c6c4d4b68a55fbf116d7fd56ee578e977239cb5c0dc6075`。
本地核对全部源产物哈希及空未定义符号表；实际未剥离内核的
`sched_clutch_steal_thread` 已包含对 `lck_ticket_unlock` 的调用。
总 make 仍退出 2，不表示全部上游导出目标通过。
打包 `40443217086` 成功，新候选为
`dist/chiron-driver-candidates/TOUCH-SMP8-QOS-40443217086/MIX2-EXPERIMENTAL-TOUCH-SMP8-QOS.img`，
SHA-256 `a699eb1e7779b406ed8f530c2b8d4ae7800a658e5fb339f06097c0ec03752be8`。
本地容器 SHA-256/legacy SHA-1、FAT/EFI/实际新内核来源、八核参数和容量绑定通过；
固件、DTB、入口及已发布 QoS/HOME 小卷固定复用。
同目录 START 给出本轮启动与
`out/chiron-touch-smp8-qos-40443217086-run1.log` 取证命令。
验收为越过原 CPU1 附近的锁 panic，继续启动 CPU2–CPU7 并观察是否进入桌面；
无新增 QEMU/硬件资格，设备启动、上传及重启均按说明手动执行。

### 修复候选的独立云端 QEMU

实测反馈修复候选进入系统时强制重启，尚无本轮真机日志，不能确定是 panic、
硬件复位或原有 300 秒返回。新增独立 `hackios-chiron-smp-qemu.yml`，
复用源构建 40442787624 的原内核、镜像 40443217086 中的 QoS/HOME 小卷
及固定哈希的大系统卷，不编译新内核或下载 IPSW。
测试 loader 单独编译并匹配 QEMU 的四核一簇 MPIDR；先从真实 QEMU DTB
检查 0–3/0x100–0x103，再运行八核双簇、540 秒预算并保存串口与截图。
QEMU 使用 Cortex-A53、虚拟 PSCI/GIC 和关闭的手机触摸硬件；
不会把结果授予 Kryo 异构核、QUP、手机固件或看门狗资格。结果待云端执行。
首轮 40445035503 停在旧大系统卷 artifact 缺失，QEMU 尚未执行。
改为把本机哈希已确认的原卷上传到有时限的 Actions artifact，
由同一 job 校验、使用；不提交系统卷或上传授权 URL 到 Git。
同时修正小卷清单带 CRC32 时的比较，只按既有门禁比较字节数及 SHA-256。

实际运行 `40452995907` 已取得**八核上线后 panic**，系统启动资格失败。
原卷补传保存于运行 40446750621 的限时 `chiron-ios165-system-replay` artifact；
该运行自身停在旧机型缺失 option ROM，未执行 XNU。
最终入口使用 QEMU virt/GICv3 的 20 个虚拟槽位，从真实 DTB 选择
0–3/0x100–0x103 八个 affinity，loader 确认另外 12 个为 OFF；
将测试 loader 的 GICR 区间扩为 20 帧，仅用于模拟器。该 loader 的实际 ARM64
目标文件编译通过，手机 loader/内核及系统输入均未修改。

`dist/chiron-smp-qemu-40452995907/inputs.json` 确认实际内核仍为 4139b5db、
QoS/HOME 小卷 f08dd1fe、原大卷 f781f611。原始串口显示 CPU1–CPU7 全部
`arm_cpu_init(): ... online`，以及 `GenericARMPE: 8 of 8 processors are running`。
随后 CPU1 报
`Ticket lock ... unexpectedly not owned ... @lock_ticket.c:152`；
约 18 秒即结束，未进入桌面，未到 540 秒测试返回。
原始日志及结构化失败结果分别为该目录的 `qemu-serial.log`、`qemu-result.json`。

同一内核符号表将锁定位为 `pset0+0x100`，调用链为
`sched_clutch_processor_enqueue+0x1c0 → thread_setrun → clear_wait →
thread_start → _IOConfigThread::configThread → GenericARMPE::start`。
反汇编在 enqueue 返回地址前直接断言持有 `pset0` 锁。
源码的非 CONFIG_SCHED_EDGE 分支在共享 bucket 统计中固定使用
`pset0.pset_clutch_root`，与两个独立 pset 的当前配置不兼容。
上一轮空回调解锁只修复了首个启动故障，仍未完成双簇调度适配；
下一轮应修正调度配置/共享统计的锁归属，不能删除锁断言来授予运行资格。
该软件缺陷已在同构 A53 模型复现，但不能据此断言手机此次重启的精确原因。
手机只读 ADB 记录为 bootreason=bootloader、pstore 空，未取得本轮 XNU 真机日志。

### 四核触摸组合

按现有双核组合新增 `touch-smp4` / `touch-smp4-qos`，使用同簇 CPU0–CPU3。
沿用触摸、HOME、QoS、300 秒返回及严格收核检查；复用固定用户态、固件和系统卷，
云端独立编译四核内核与 loader。不修改 SMP ABI、PSCI、GIC 或调度实现。
本地 7 项变体、25 项载荷检查通过，组合用例覆盖 2/4/8；
四核触摸 loader ARM64 目标文件编译及交接前 OFF 检查通过。
源构建 `40445633354`、打包 `40445959350` 成功，提交
`2f22672ea9616c9b2d8aac36cfe3c9a263e7c523`；候选
`dist/chiron-driver-candidates/TOUCH-SMP4-QOS-40445959350/MIX2-EXPERIMENTAL-TOUCH-SMP4-QOS.img`，
SHA-256 `45d02f3635dd44f653c7488a5a19ef6578d63e5aca573de36a15a92abef57cc7`。
内核 SHA-256 `88da5b71ce299f17cb480f331eca5e83a454126efafb8b740fbf43769a374157`，
已链接且未定义符号为空；总 make=2 仍来自上游导出名单检查。
本地容器/legacy SHA-1、FAT/EFI、实际内核来源、四核参数及容量绑定通过，
与双核包直接比较确认固件、入口、大小系统卷及非核数 profile/运行参数一致。
同目录 START 提供启动与本轮日志命令；未进行四核 QEMU/真机运行，
不继承双核实测反馈的运行资格。

### Clutch 单一调度集合修正

本轮只修改 CHIRON_SMP + 普通 Clutch 的 CPU 注册，将所有八核关联到
`pset0`，满足非 Edge Clutch 共享 bucket 统计的锁约定。
物理 `cpu_cluster_id`、两簇拓扑、MPIDR、cluster master、PSCI/GIC、
启动等待和严格收核均保留；没有删除断言或开启 Apple 专用寄存器路径。
目标为双簇八核在共享调度集合中完成系统启动，大小核调优另行验证。

新增回归执行真实的 bucket 统计函数及注册选集片段：旧配置在第二簇触发
持锁失败，新配置更新八核统计通过，并检查每核物理簇字段保留；
非 CHIRON 构建仍走原分支。12 项 SMP 检查无跳过，另有 7 项变体、
25 项载荷和 4 项 QEMU 判定检查通过。
云端 QEMU 入口改为显式输入匹配的源构建/镜像编号，
并要求两簇实际关联到同一个 pset；新构建、运行及候选结果待取得。

源构建 40457193107、打包 40457536115 成功，源码提交
`73ab5afe7a7f2b81967c4ba6846efb3c7f5bc298`。
新内核 SHA-256 为 `a2882eecafd6377538efd28d7bd476c42e9a70884fc5711155d822532c25bea5`；
未定义符号为空，仍保留总 make=2，不表示全部上游导出目标通过。
实际 QEMU 运行 40457857698 显示七个次核全部在线、两簇均使用 pset0，
无 panic，540 秒测试定时返回；宿主进程持续 569.265 秒。
420/500 秒截图已目视确认为 SpringBoard 桌面，后期仍有持续换帧。

该运行最初被自动判据误判：固定 QoS/HOME 小卷没有输出判据期待的 ps 列表，
但 launchd 同时记录了两个桌面进程成功启动和 running 状态。
仅修正证据判据并补充定向用例（本地现共 49 项相关检查通过），
云端保存证据复核 40459663351 成功；没有重复执行内核。
原 `qemu-result.json` 的失败标记保留，新的 `qualification.json` 明确记录
runtime_run/qualification_run 和 `new_guest_execution=false`。
实际运行证据在 `dist/chiron-smp-qemu-40457857698/`，
复核证据在 `dist/chiron-smp-qemu-40459663351/`。

新交付为
`dist/chiron-driver-candidates/TOUCH-SMP8-QOS-40457536115/MIX2-EXPERIMENTAL-TOUCH-SMP8-QOS.img`，
SHA-256 `75dce1f23799a71500228ef0bb23a92fd3900650ab2ba9addc0f0b7e113c465f`。
容器/legacy SHA-1、FAT/EFI、新内核来源、八核参数及容量校验通过；
固件、DTB、入口和大小系统卷固定复用。
同目录包含 START、静态报告、独立 QEMU 复核报告和桌面截图，
启动及取日志路径均绑定本轮编号。手机仍为 300 秒测试返回；
无新增真机操作或资格，实际 Kryo 双簇、触摸和手机固件返回待实测确认。

2026-10-27 已发布 GitHub 预发布 `chiron-touch-smp-20261027`（release 425109743），
包含单核 40327776076、双核 40434498916、八核 40457536115 三份独立 ZIP，
原镜像字节保持，并配套各自 manifest、上传脚本、通用路径说明及校验文件。
ZIP 逐项读回及五个发布附件的 GitHub 服务端 SHA-256 全部一致；
原单核发布保留，大系统卷及私人设备日志未上传。没有新增构建或运行资格。

2026-10-27 实测确认最新八核组合可用，补充为八核组合的现场观察记录。
后续迁移以现有进系统、触摸和八核实现为保留基线；
本轮未新增读取该次实机日志或核对运行镜像哈希，不扩展为长期稳定性结论。
