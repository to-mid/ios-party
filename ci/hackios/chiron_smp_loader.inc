/* Included after psci_call(). No CPU_ON is issued by this preflight. */
#define CHIRON_SMP_ABI_MAGIC 0x31504d534e524843ULL
#define CHIRON_SMP_ABI_CHECK 0xceafb2acb1adb7bcULL

static int starts_with(const char *text, const char *prefix);

static const uint64_t chiron_smp_mpidrs[8] = {
	0, 1, 2, 3, 256, 257, 258, 259
};

static int
chiron_smp_count_valid(int count)
{
	return count == 1 || count == 2 || count == 4 || count == 8;
}

/* Match whole space-delimited tokens; reject ambiguous or conflicting selectors. */
static int
chiron_smp_parse(const char *args, int *count)
{
	int seen = 0;
	*count = 1;
	for (const char *p = args; *p;) {
		const char *token;
		uint64_t length;
		while (*p == ' ') p++;
		if (!*p) break;
		token = p;
		while (*p && *p != ' ') {
			if ((unsigned char)*p < 32 || (unsigned char)*p == 127) return 0;
			p++;
		}
		length = (uint64_t)(p - token);
		if ((length == 4 && starts_with(token, "cpus")) ||
		    (length == 7 && (starts_with(token, "bootcpu") || starts_with(token, "cpumask")))) return 0;
		if (starts_with(token, "bootcpu=") || starts_with(token, "cpumask=")) return 0;
		if (starts_with(token, "cpus=")) {
			if (seen++ || length != 6 || token[5] < '1' || token[5] > '8') return 0;
			*count = token[5] - '0';
		}
	}
	return chiron_smp_count_valid(*count) && *count <= CHIRON_SMP_LIMIT;
}

static int
chiron_smp_kernel_capable(uint64_t lo, uint64_t hi)
{
	if (hi < lo || hi - lo < 16 || (lo & 7)) return 0;
	for (uint64_t address = lo; address <= hi - 16; address += 8) {
		const uint64_t *words = (const uint64_t *)(uintptr_t)address;
		if (words[0] == CHIRON_SMP_ABI_MAGIC && words[1] == CHIRON_SMP_ABI_CHECK) return 1;
	}
	return 0;
}

static uint64_t
chiron_smp_read_typer(uint64_t offset)
{
	return *(volatile uint64_t *)(uintptr_t)(PLAT->gic_redist + offset + 8);
}

static int
chiron_smp_redistributors(int count)
{
	unsigned found = 0;
	for (uint64_t offset = 0; offset < 0x100000; offset += 0x20000) {
		uint64_t typer = chiron_smp_read_typer(offset);
		uint32_t affinity = (uint32_t)(typer >> 32);
		/* VLPIS changes the frame stride; this port supports GICv3 frames only. */
		if (typer & (1ULL << 1)) return 0;
		for (int cpu = 0; cpu < count; cpu++) {
			if (affinity == chiron_smp_mpidrs[cpu]) {
				if (found & (1u << cpu)) return 0;
				found |= 1u << cpu;
			}
		}
		if (typer & (1ULL << 4)) break;
	}
	return found == (1u << count) - 1;
}

static int
chiron_smp_prepare(const char *args, uint64_t boot, uint64_t kernel,
    uint64_t lo, uint64_t hi, uint64_t cpus[8], int *count)
{
	*count = 0;
	int requested;
	if (boot != 0 || kernel != boot || !chiron_smp_parse(args, &requested)) {
		print("chiron smp: unsupported CPU selector or boot affinity; refusing boot\n");
		return 0;
	}
	if (requested > 1) {
		if (!chiron_smp_kernel_capable(lo, hi)) {
			print("chiron smp: kernel lacks SMP ABI v1; rebuild kernel\n");
			return 0;
		}
		if (PLAT->psci_hvc || PLAT->gic_dist != 0x17a00000 ||
		    PLAT->gic_redist != 0x17b00000 || PLAT->virtual_timer_intid != 19) {
			print("chiron smp: unexpected platform configuration\n");
			return 0;
		}
		int64_t version = psci_call(0x84000000u, 0, 0, 0);
		/* PSCI 0.2 or any released 1.x interface; reject unknown major versions. */
		if (version < 0 || ((uint64_t)version >> 16) > 1 || version < 2) {
			print("chiron smp: PSCI 0.2/1.x required\n");
			return 0;
		}
		for (int cpu = 1; cpu < requested; cpu++) {
			int64_t state = psci_call(PSCI_AFFINITY_INFO, chiron_smp_mpidrs[cpu], 0, 0);
			/* NOT_SUPPORTED is insufficient evidence of firmware ownership. */
			if (state != 1) {
				print("chiron smp: MPIDR %x is not confirmed OFF (%d)\n",
				    chiron_smp_mpidrs[cpu], (uint64_t)state);
				return 0;
			}
		}
		if (!chiron_smp_redistributors(requested)) {
			print("chiron smp: requested redistributors missing or unsupported\n");
			return 0;
		}
	}
	for (int cpu = 0; cpu < requested; cpu++) cpus[cpu] = chiron_smp_mpidrs[cpu];
	*count = requested;
	print("chiron smp: selected %d CPUs (build limit %d)\n",
	    (uint64_t)requested, (uint64_t)CHIRON_SMP_LIMIT);
	return 1;
}
