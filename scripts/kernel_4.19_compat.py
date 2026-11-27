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
    if "copy_to_kernel_nofault" not in s:
        raise SystemExit("KernelSU-Next patch_memory.c: expected API call not found")

    # Ensure the legacy probe_kernel_write() declaration is visible before
    # the compatibility macro is expanded on Linux 4.19.
    include_anchor = '#include "patch_memory.h"'
    if '#include <linux/uaccess.h>' not in s:
        if include_anchor not in s:
            raise SystemExit("KernelSU-Next patch_memory.c: cannot find include anchor")
        s = s.replace(include_anchor,
                      include_anchor + '\\n#include <linux/uaccess.h>', 1)

    if "#define copy_to_kernel_nofault" not in s:
        compat = '''
#include <linux/version.h>

/* copy_to_kernel_nofault() was named probe_kernel_write() before 5.8. */
#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 8, 0)
#define copy_to_kernel_nofault(dst, src, size) \\
    probe_kernel_write((dst), (src), (size))
#endif
'''
        if include_anchor not in s:
            raise SystemExit("KernelSU-Next patch_memory.c: cannot find include anchor")
        s = s.replace(include_anchor, include_anchor + compat, 1)
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
edit("drivers/kernelsu/hook/patch_memory.c", ksun_patch_memory)
edit("drivers/media/v4l2-core/v4l2-mem2mem.c", v4l2_m2m)

edit("include/linux/pid.h", pid_header)
edit("include/linux/fscrypt.h", fscrypt_header)
edit("mm/rmap.c", rmap_source)
edit("kernel/cred.c", cred_source)
print("[compat] Linux 4.19 API compatibility checks passed")
