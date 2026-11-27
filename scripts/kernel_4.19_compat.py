#!/usr/bin/env python3
"""Backport small API compatibility fixes for the Samsung Linux 4.19.325 tree."""
from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "android-kernel")
if not (root / "Makefile").is_file():
    raise SystemExit(f"kernel source tree not found: {root}")

def edit(rel, transform):
    path = root / rel
    data = path.read_text()
    updated = transform(data)
    if updated != data:
        path.write_text(updated)
        print(f"[compat] updated {rel}")
    else:
        print(f"[compat] already compatible: {rel}")

def pid_header(s):
    anchor = "extern struct pid *get_task_pid(struct task_struct *task, enum pid_type type);"
    decl = "\n/* Used by BPF pidfd-backed task storage. */\nextern struct pid *pidfd_get_pid(int fd, unsigned int *flags);"
    if "pidfd_get_pid(int fd" in s:
        return s
    if anchor not in s:
        raise SystemExit("pid.h: cannot locate get_task_pid declaration")
    return s.replace(anchor, anchor + decl, 1)

def fscrypt_header(s):
    anchor = """const char *fscrypt_get_symlink(struct inode *inode, const void *caddr,
				unsigned int max_size,
				struct delayed_call *done);"""
    decl = anchor + "\nint fscrypt_symlink_getattr(const struct path *path, struct kstat *stat);"
    if "int fscrypt_symlink_getattr(const struct path *path" not in s:
        if anchor not in s:
            raise SystemExit("fscrypt.h: cannot locate fscrypt_get_symlink declaration")
        s = s.replace(anchor, decl, 1)

    stub_anchor = """static inline const char *fscrypt_get_symlink(struct inode *inode,
					      const void *caddr,
					      unsigned int max_size,
					      struct delayed_call *done)
{
	return ERR_PTR(-EOPNOTSUPP);
}"""
    stub = stub_anchor + """

static inline int fscrypt_symlink_getattr(const struct path *path,
					  struct kstat *stat)
{
	return -EOPNOTSUPP;
}"""
    # The declaration is inside CONFIG_FS_ENCRYPTION; the stub belongs in its
    # !CONFIG_FS_ENCRYPTION branch to keep both configurations link-safe.
    if s.count("static inline int fscrypt_symlink_getattr(") == 0:
        if stub_anchor not in s:
            raise SystemExit("fscrypt.h: cannot locate disabled-encryption symlink stub")
        s = s.replace(stub_anchor, stub, 1)
    return s

def rmap_source(s):
    old = ".anon_lock = page_lock_anon_vma_read,"
    new = ".anon_lock = page_lock_anon_vma_read_compat,"
    if old not in s:
        if new in s:
            return s
        raise SystemExit("mm/rmap.c: expected anon_lock assignments not found")
    anchor = '#include "internal.h"'
    helper = '''

/*
 * Linux 4.19 exposes page_lock_anon_vma_read(page), while the backported
 * rmap_walk_control callback carries the newer rwc argument.
 */
static struct anon_vma *
page_lock_anon_vma_read_compat(struct page *page,
			       struct rmap_walk_control *rwc)
{
	(void)rwc;
	return page_lock_anon_vma_read(page);
}
'''
    if "page_lock_anon_vma_read_compat(" not in s:
        if anchor not in s:
            raise SystemExit("mm/rmap.c: cannot locate insertion point for callback shim")
        s = s.replace(anchor, anchor + helper, 1)
    return s.replace(old, new)


def mrdump_source(s):
    # This vendor file uses a one-argument array-size helper. Keep it local
    # instead of redefining the kernel's two-argument overflow-safe array_size.
    s = s.replace("#define array_size(x) (sizeof(x) / sizeof((x)[0]))",
                  "#define mboot_array_size(x) (sizeof(x) / sizeof((x)[0]))")
    s = s.replace("array_size(aee_rr_", "mboot_array_size(aee_rr_")
    if "#define array_size(x)" in s:
        raise SystemExit("mboot_params.c: conflicting array_size macro remains")
    if "mboot_array_size(aee_rr_" not in s:
        raise SystemExit("mboot_params.c: expected array-size callsites not found")
    return s

def ksun_patch_memory(s):
    # Linux 4.19 exposes probe_kernel_write(), not copy_to_kernel_nofault().
    # The old shim relied on a version.h macro that did not expand reliably
    # in the Samsung vendor build. Call the native 4.19 API directly instead.
    old_call = "ret = (int)copy_to_kernel_nofault(map, src, len);"
    new_call = "ret = (int)probe_kernel_write(map, src, len);"
    if old_call in s:
        s = s.replace(old_call, new_call)
    if old_call in s:
        raise SystemExit("KernelSU-Next patch_memory.c: copy_to_kernel_nofault call remains")
    if s.count(new_call) != 2:
        raise SystemExit("KernelSU-Next patch_memory.c: expected both ARM64/x86_64 write paths")
    if '#include "linux/uaccess.h"' not in s or "#include <linux/uaccess.h>" not in s:
        raise SystemExit("KernelSU-Next patch_memory.c: uaccess headers missing for probe_kernel_write")
    return s

def cred_source(s):
    # struct cred::usage is atomic_long_t in this vendor tree; the atomic_t
    # helper is a type mismatch and fails Clang's -Werror check.
    old = "atomic_inc_not_zero(&((struct cred *)cred)->usage)"
    new = "atomic_long_inc_not_zero(&((struct cred *)cred)->usage)"
    count = s.count(old)
    if count:
        s = s.replace(old, new)
    if old in s:
        raise SystemExit("kernel/cred.c: atomic_t credential refcount call remains")
    if new not in s:
        raise SystemExit("kernel/cred.c: expected credential refcount callsites not found")
    return s

def mtk_unused_variables(s):
    old = "static unsigned int gConnAdpDbgLvl = CONNADP_LOG_INFO;"
    new = "static unsigned int gConnAdpDbgLvl __maybe_unused = CONNADP_LOG_INFO;"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("wmt_build_in_adapter.c: debug-level variable annotation failed")
    if new not in s:
        raise SystemExit("wmt_build_in_adapter.c: expected debug-level variable not found")
    return s

def wlan_chip_id(s):
    old = "uint32_t chip_id = prChipInfo->chip_id;"
    new = "uint32_t chip_id __maybe_unused = prChipInfo->chip_id;"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("wlan_lib.c: chip_id unused-variable annotation failed")
    if new not in s:
        raise SystemExit("wlan_lib.c: expected chip_id declaration not found")
    return s

def dpmaif_pointer_format(s):
    replacements = {
        "(u32)data_64ptr": "(u32)(unsigned long)data_64ptr",
        "(u32)data_8ptr": "(u32)(unsigned long)data_8ptr",
    }
    for old, new in replacements.items():
        if old in s:
            s = s.replace(old, new)
        if old in s:
            raise SystemExit("ccci_hif_dpmaif.c: pointer-to-u32 cast remains")
        if new not in s:
            raise SystemExit("ccci_hif_dpmaif.c: expected debug pointer cast not found")
    return s


def wlan_oid_unused_ps_mode(s):
    # MediaTek's WLAN debug string table is referenced only by DBGLOG.
    # When logging is compiled out, Clang diagnoses it as unused under Werror.
    old = "const uint8_t *apucPsMode[Param_PowerModeMax] = {"
    new = "const uint8_t *apucPsMode[Param_PowerModeMax] __maybe_unused = {"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("wlan_oid.c: apucPsMode unused annotation failed")
    if new not in s:
        raise SystemExit("wlan_oid.c: expected apucPsMode declaration not found")
    return s


def wlan_nic_tx_unused_rawtxs(s):
    # pu4RawTxs is consumed only by DBGLOG_LIMITED; logging-off builds
    # compile those callsites away, so preserve the pointer but annotate it.
    old = "uint32_t *pu4RawTxs ="
    new = "uint32_t *pu4RawTxs __maybe_unused ="
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("nic_tx.c: pu4RawTxs unused annotation failed")
    if new not in s:
        raise SystemExit("nic_tx.c: expected pu4RawTxs declaration not found")
    return s

def v4l2_m2m(s):
    old = """struct video_device *vdev = video_devdata(file);
\tstruct vb2_queue *vq;
\tint ret;

\tvq = v4l2_m2m_get_vq(m2m_ctx, buf->type);
\tret = vb2_prepare_buf(vq, buf, NULL);"""
    new = """struct video_device *vdev = video_devdata(file);
\tstruct vb2_queue *vq;
\tint ret;

\tvq = v4l2_m2m_get_vq(m2m_ctx, buf->type);
\tret = vb2_prepare_buf(vq, vdev->v4l2_dev->mdev, buf);"""
    if old in s:
        return s.replace(old, new, 1)
    if "vb2_prepare_buf(vq, vdev->v4l2_dev->mdev, buf)" in s:
        return s
    raise SystemExit("v4l2-mem2mem.c: cannot locate prepare_buf call")

edit("drivers/misc/mediatek/aee/mrdump/mboot_params.c", mrdump_source)
edit("drivers/misc/mediatek/connectivity/common/wmt_build_in_adapter.c", mtk_unused_variables)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/common/wlan_lib.c", wlan_chip_id)
edit("drivers/misc/mediatek/eccci/hif/ccci_hif_dpmaif.c", dpmaif_pointer_format)
edit("drivers/kernelsu/hook/patch_memory.c", ksun_patch_memory)
edit("drivers/media/v4l2-core/v4l2-mem2mem.c", v4l2_m2m)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/common/wlan_oid.c", wlan_oid_unused_ps_mode)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/nic/nic_tx.c", wlan_nic_tx_unused_rawtxs)

edit("include/linux/pid.h", pid_header)
edit("include/linux/fscrypt.h", fscrypt_header)
edit("mm/rmap.c", rmap_source)
edit("kernel/cred.c", cred_source)
print("[compat] Linux 4.19 API compatibility checks passed")
