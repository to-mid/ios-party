# CHIRON BLSP1 / QUP5 时钟准备

本次只新增独立时钟模块、主机夹具、测试和本文档。默认硬件行为仍关闭。
父任务通过显式 `chiron_touch_clock_probe=1` 调用准备函数；成功后可立即调用现有
`chiron_board_probe()` 继续只读诊断，再主动 panic。时钟准备本身不读取 GPIO、GIC
或 QUP 控制器，不启动触摸工作线程，不进行 I2C 事务。

## 实机输入

`out/chiron-touch-probe-usb-20261024.log` 的 211 行起记录了真实 USB 探针结果：
`status=-22 failed=1 observed=3f handoff=1/1/fe/1`。

| 寄存器 | 物理地址 | 实测值 |
| --- | --- | --- |
| BLSP1_BCR | `0x117000` | `0x00000000` |
| APCS_BRANCH_VOTE | `0x152004` | `0x80889d80` |
| BLSP1_AHB_CBCR | `0x117004` | `0x8000c220` |
| QUP5_I2C_CBCR | `0x121008` | `0x80000000` |
| QUP5_I2C_CMD_RCGR | `0x121020` | `0x80000000` |
| QUP5_I2C_CFG_RCGR | `0x121024` | `0x00000000` |

此记录证明 AHB vote bit17 缺失，AHB/QUP/root 显示关闭；GPIO/GIC/QUP 状态尚未读取。
它不证明新增初始化已经能在硬件上启动时钟，更不证明触摸可用。

## 固定源码依据

仓库为 `LineageOS/android_kernel_xiaomi_msm8998`，提交
`7231883a368604f90806e167890e00f515a9927c`。以下本机文件的 SHA256 已与现有
`ci/hackios/chiron_touch_sources.json` 核对，测试会在文件存在时再次验证：

| 本机文件 | SHA256 |
| --- | --- |
| `/private/tmp/chiron-clock-gcc-8998.c` | `409bb76da8b033df94c63884d0cd2596b645c3d3111f8576f91f66c954447452` |
| `/private/tmp/chiron-clock-local2.c` | `a467ad3783a14c5f69c87834d042a9fdc4d85f2933ec3e5908824b42e29bc3e3` |
| `/private/tmp/chiron-msm-clocks-hwio-8998.h` | `38b29e3327e977d2d8e405ff73d8a856536fd0ef8d9e1c0c44ea32394ac86461` |

`clock-gcc-8998.c:40-58` 定义 XO 源选择为 0、`FIXDIV(1)=1`；
`:309-313` 的 I2C 频表支持 19.2MHz XO 和 50MHz GPLL0。
`:442-454` 的 QUP5 I2C root 使用 `set_rate_hid`，其
`non_local_children`、`non_local_control_timeout`、`force_enable_rcgr` 均未设置。
`:1218-1229` 指定 BLSP1 AHB 使用 APCS vote bit17，
`:1327-1337` 指定 QUP5 分支及其 root 父时钟。
HWIO 头文件给出上述地址偏移；CFG 在 CMD 后 `+4`。

采用的顺序及其源码依据：

1. `local_vote_clk_enable()` (`clock-local2.c:1161-1180`) 对 vote 做读改写，
   然后检查 AHB halt。`branch_clk_halt_check()` (`:635-677`) 在检查前执行内存屏障；
   状态检查使用完整的 `[31:28]`，允许 ON=0 或 NOC_FSM_ON=2，OFF=8。
2. `set_rate_hid()` / `__set_rate_hid()` (`:150-169`) 只改变 CFG 源选择和
   HID 分频位，保持其余位。源选择 0、分频编码 1 对应频表中的 XO 19.2MHz。
   模块要求 MND 模式已经为 0，不写 M/N/D。
3. `rcg_update_config()` (`:109-130`) 读取 CMD、置 UPDATE bit0，并等待其自清；
   本模块同时要求事务的 DIRTY `[7:4]` 清零。
4. `branch_clk_enable()` (`:811-843`) 对 CBCR 置 bit0，屏障后检查分支 halt；
   本模块再要求 root OFF bit31 清零，并重新读取六个 GCC 寄存器验证最终组合。

已核对 force-root 路径：`rcg_set_force_enable()` / `rcg_clear_force_enable()`
(`:201-225`) 操作 CMD bit1，后者含 100us 延时；
`rcg_clk_set_rate()` (`:350-414`) 只在有非本地子时钟或非本地控制超时时使用该路径。
QUP5 的这两个条件均为 0，使用普通 `set_rate_hid()` 分支。因此这里不发明额外的
force-root 序列，预检发现 force、pending 或 dirty 时直接拒绝。

**电压前提：** QUP5 root 的 `VDD_DIG_FMAX_MAP2(LOWER, 19200000, LOW, 50000000)`
由 Linux 框架执行电压投票。本模块没有复现 RPM/PMIC/VDD 投票，也不宣称它可被普遍省略。
这个有限诊断仅尝试最低频率 XO 19.2MHz，假定当前已运行 SoC 的供电足以维持它；
该前提尚未经过新的硬件验证。若分支/root 无法启动，有限等待失败并尝试回滚，
不会升级到 GPLL0、50MHz、PMIC 调压或强制 root。

## API 和接入约定

```c
int chiron_clock_prepare(const struct chiron_clock_io *io,
                        struct chiron_clock_report *report);
```

`chiron_clock_io` 保持约定字段：`board, write32, barrier, now_us, delay_us`。
所有回调都使用 `board.context`，读取继续复用 `chiron_board_io`。
调用者必须提供有界的同步回调、正确的 device MMIO 映射和完整设备内存屏障，
串行调用，并保证固件及其余七个 CPU 不会并发操作这些时钟。
模块没有全局状态，不解析启动参数，不自行初始化硬件。

`before/after` 保持 `chiron_board_report` 类型；这里只观察六个 GCC 单元，
完整观察的 `observed` 为 `0x3f`。`after` 表示最后成功读取到的单元，包含失败后
尽力重读的结果，不能视为硬件原子快照。预检读取失败时 `before` 可能只有身份信息。
模块不把“时钟成功”写成“板级就绪”；父任务仍需原有板级诊断。

`result` 和返回值一致；`step` 是 `chiron_clock_step` 的失败阶段或 `CC_DONE`。
保留 `changed` 和 `rollback` 字段，新增以下诊断：

| 字段 | 含义 |
| --- | --- |
| `changed` | 按 `CB_*` 索引的写入尝试位图，写回调失败也计算，因为硬件可能已接受写入 |
| `remaining` | 尚未证明撤销的本次修改位图；成功时保留，供后续立即诊断使用 |
| `rollback` | 0 无需回滚/成功未回滚；1 完成；2 未完成；3 所有权丢失停止 |
| `cleanup_result/cleanup_step` | 回滚错误和停止阶段，不覆盖主错误 |
| `failed_register/cleanup_register` | 主错误与回滚错误的寄存器索引，无法确定时为 -1 |

板级身份/读取/所有权错误沿用 `CB_PLATFORM/CB_READ_FAILED/CB_OWNERSHIP`。
模块自有错误为 `CHIRON_CLOCK_BAD_IO=-60`、`PROFILE=-61`、`WRITE=-62`、
`TIMEOUT=-63`、`READBACK=-64`、`TIMER=-65`、`CHANGED=-66`。

## 写入边界与失败处理

写入严格限于四个地址：AHB vote、QUP5 root CFG、root CMD、QUP5 I2C CBCR。
不写 BLSP1 BCR/AHB CBCR，不切换 BLSP reset，不改 GPIO/PMIC/IRQ/QUP。
先用现有 `chiron_board_identity()` 验证平台及 `1/1/fe/1` 直接启动交接，
身份或所有权预检失败时为零 MMIO 写入，且不进行 GCC 读取。
每次写前重新检查身份、交接、BCR 和目标位，写后屏障及回读；
所有权校验失败会在本次调用中保持锁定，即使下一次属性又出现也不恢复写入。

只接受已关闭且 clean 的 XO 复位/已配置状态，或完整匹配的已经开启状态。
已经开启且匹配的 QUP5 返回成功、零写入。已有 AHB vote 绝不撤销；
其他用户维持 AHB 开启时，允许添加本次 vote，但失败只移除新增 bit17。
所有修改都读取当前值后仅替换所属位，保留无关位。
阶段边界重读 GCC，检测已开启后再次停止的时钟，不能仅凭一次 halt 结果继续。

每个等待同时具有 **500us 经过时间上限**和 **500 次迭代上限**，
每次不满足条件仅延时 1us。检查读取前后及成功返回前的时间；
计时倒退报 `TIMER`，计时停滞仍由迭代上限终止，过度延时不会被当成及时成功。
这些限制不可能约束一个内部永久挂住的外部回调，因此回调有界是必要前提。

失败回滚按反向依赖执行：

1. 仅在本次尝试过分支开启时，依照 `branch_clk_disable()` (`:785-809`)
   清 bit0、屏障、等 OFF，再等 root OFF。失败即停止，不继续改 CFG。
2. 仅在本次尝试过 CFG/CMD 时，先有限等待 UPDATE 结束，分支和 root 必须关闭。
   允许属于本次未提交 CFG 的 DIRTY shadow，恢复原始 HID/XO 位，再按同一
   `rcg_update_config()` 握手重新锁存。原始实测 CFG=0 也是源代码 `cxo_f`
   (`:81-87`) 使用的 XO 编码。绝不写 UPDATE=0 伪造取消，不写 force-root。
3. 仅在 bit17 原先缺失且本次尝试添加时，按 `local_vote_clk_disable()`
   (`:1147-1159`) 清新增位。该函数不等待 AHB 关闭，其他投票可继续保持它开启。

若 UPDATE 永远不结束、halt 无法确认、写回调/读取失败、目标控制位改变、
BCR 变化或所有权丢失，回滚可能无法完成。报告保留 `remaining` 和回滚错误；
不继续尝试 reset、取消事务或反复写入。仅有交接属性不能阻止安全世界/外部固件
在两次检查之间改变硬件；这不是具备跨固件原子所有权的完整时钟框架。

## 验证

```sh
python3 -m unittest discover -s tests -p test_chiron_touch_clock.py -v
```

10 项测试通过，C 夹具使用 ASan/UBSan。测试包含真实捕获值、
四地址写白名单、写入顺序与紧邻屏障、幂等已开启路径、保留预先开启的时钟、
无关位/其他 AHB vote、身份及每个交接字段拒绝、未知 active/dirty/forced 状态、
成功路径每个 MMIO 读取点及每次写入的故障注入（含失败写实际生效）、
被忽略的写入、halt/root/update 超时、时钟回退、计时停滞/倒退/过度延时、
所有权及属性读取丢失、回滚每次写入失败和回滚 halt 卡住。
另有 ARM64 Darwin freestanding 语法检查和三个固定源码的哈希验证。

测试是寄存器模型与语法验证。未执行设备命令、完整 XNU 编译链接或新的实机启动；
本模块成功路径在硬件上的结果、VDD 前提及触摸功能仍待父任务生成的诊断镜像验证。
