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

edit("include/linux/pid.h", pid_header)
edit("include/linux/fscrypt.h", fscrypt_header)
edit("mm/rmap.c", rmap_source)
print("[compat] Linux 4.19 API compatibility checks passed")
