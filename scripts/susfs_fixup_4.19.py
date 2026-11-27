#!/usr/bin/env python3
"""Finish the SUSFS v1.5.5 patch on Samsung's MTK 4.19.325 fs_context tree.

The upstream non-GKI 4.19 patch targets vanilla VFS call sites. This A13VE
source has Samsung's fs_context backport and vendor additions, so the
matching hunks are applied from the pinned Samsung fs_context patch and the
few vendor-specific insertion points are handled here explicitly.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: susfs_fixup_4.19.py KERNEL_DIR")

root = Path(sys.argv[1]).resolve()

def read(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing required kernel file: {rel}")
    return p.read_text()

def write(rel, data):
    (root / rel).write_text(data)

def replace_once(rel, old, new, marker):
    data = read(rel)
    if marker in data:
        return
    count = data.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected one insertion anchor for {marker!r}, found {count}")
    write(rel, data.replace(old, new, 1))

def insert_after_once(rel, anchor, addition, marker):
    data = read(rel)
    if marker in data:
        return
    count = data.count(anchor)
    if count != 1:
        raise SystemExit(f"{rel}: expected one anchor for {marker!r}, found {count}")
    write(rel, data.replace(anchor, anchor + addition, 1))

# fs/Makefile: the source has extra fs_context objects after the vanilla 4.19
# list, so place susfs.o after the complete obj-y list instead of line numbers.
insert_after_once(
    "fs/Makefile",
    "\t\tfs_context.o fs_parser.o\n",
    "\nobj-$(CONFIG_KSU_SUSFS) += susfs.o\n",
    "obj-$(CONFIG_KSU_SUSFS) += susfs.o",
)

# dcache.c has a vendor KDP include block after mount.h. Add SUSFS before the
# internal VFS headers so the definitions are available to both lookups.
insert_after_once(
    "fs/dcache.c",
    "#include <linux/list_lru.h>\n",
    "#ifdef CONFIG_KSU_SUSFS_SUS_PATH\n#include <linux/susfs_def.h>\n#endif\n",
    "#include <linux/susfs_def.h>",
)

# This tree's may_create() takes a vfsmount and uses inode_permission2().
replace_once(
    "fs/namei.c",
    """static inline int may_create(struct vfsmount *mnt, struct inode *dir, struct dentry *child)
{
\tstruct user_namespace *s_user_ns;
\taudit_inode_child(dir, child, AUDIT_TYPE_CHILD_CREATE);
\tif (child->d_inode)
""",
    """static inline int may_create(struct vfsmount *mnt, struct inode *dir, struct dentry *child)
{
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
\tint error;
#endif
\tstruct user_namespace *s_user_ns;
\taudit_inode_child(dir, child, AUDIT_TYPE_CHILD_CREATE);
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
\tif (child->d_inode && unlikely(child->d_inode->i_state & INODE_STATE_SUS_PATH) && likely(current->susfs_task_state & TASK_STRUCT_NON_ROOT_USER_APP_PROC)) {
\t\terror = inode_permission2(mnt, dir, MAY_WRITE | MAY_EXEC);
\t\tif (error)
\t\t\treturn error;
\t\treturn -ENOENT;
\t}
#endif
\tif (child->d_inode)
""",
    "INODE_STATE_SUS_PATH",
)

# fs/namespace.c: vendor source has fs_context plus KDP_NS conditional code.
replace_once(
    "fs/namespace.c",
    """#include <linux/fs_context.h>

#include "pnode.h"
#include "internal.h"

""",
    """#include <linux/fs_context.h>
#if defined(CONFIG_KSU_SUSFS_SUS_MOUNT) || defined(CONFIG_KSU_SUSFS_TRY_UMOUNT)
#include <linux/susfs_def.h>
#endif

#include "pnode.h"
#include "internal.h"

#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
extern bool susfs_is_current_ksu_domain(void);
extern bool susfs_is_current_zygote_domain(void);

static DEFINE_IDA(susfs_mnt_id_ida);
static DEFINE_IDA(susfs_mnt_group_ida);

#define CL_ZYGOTE_COPY_MNT_NS BIT(24)
#define CL_COPY_MNT_NS BIT(25)
#endif

#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
extern void susfs_auto_add_sus_ksu_default_mount(const char __user *to_pathname);
bool susfs_is_auto_add_sus_ksu_default_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT
extern int susfs_auto_add_sus_bind_mount(const char *pathname, struct path *path_target);
bool susfs_is_auto_add_sus_bind_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT
extern void susfs_auto_add_try_umount_for_bind_mount(struct path *path);
bool susfs_is_auto_add_try_umount_for_bind_mount_enabled = true;
#endif

""",
    "susfs_is_current_ksu_domain",
)

# alloc_vfsmnt(): allow SUSFS mounts to use the separate high mount-ID range.
replace_once(
    "fs/namespace.c",
    """static struct mount *alloc_vfsmnt(const char *name)
{
""",
    """#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
static struct mount *alloc_vfsmnt(const char *name, bool should_spoof, int custom_mnt_id)
#else
static struct mount *alloc_vfsmnt(const char *name)
#endif
{
""",
    "bool should_spoof, int custom_mnt_id",
)
replace_once(
    "fs/namespace.c",
    """\t\terr = mnt_alloc_id(mnt);
\t\tif (err)
\t\t\tgoto out_free_cache;
#ifdef CONFIG_KDP_NS
""",
    """#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\t\tif (should_spoof) {
\t\t\tif (!custom_mnt_id) {
\t\t\t\terr = susfs_mnt_alloc_id(mnt);
\t\t\t} else {
\t\t\t\tmnt->mnt_id = custom_mnt_id;
\t\t\t\terr = 0;
\t\t\t}
\t\t\tgoto bypass_orig_flow;
\t\t}
#endif
\t\terr = mnt_alloc_id(mnt);
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
bypass_orig_flow:
#endif
\t\tif (err)
\t\t\tgoto out_free_cache;
#ifdef CONFIG_KDP_NS
""",
    "susfs_mnt_alloc_id(mnt)",
)

# vfs_create_mount() is the Samsung fs_context allocation path.
replace_once(
    "fs/namespace.c",
    """\tmnt = alloc_vfsmnt(fc->source ?: "none");
""",
    """#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tif (unlikely(susfs_is_current_ksu_domain())) {
\t\tmnt = alloc_vfsmnt(fc->source ?: "none", true, 0);
\t\tgoto susfs_vfs_create_mount_done;
\t}
\tmnt = alloc_vfsmnt(fc->source ?: "none", false, 0);
susfs_vfs_create_mount_done:
#else
\tmnt = alloc_vfsmnt(fc->source ?: "none");
#endif
""",
    "susfs_vfs_create_mount_done:",
)

# clone_mnt(): the main SUSFS patch already adds the KSU/zygote selection
# logic; this missing hunk handles the ordinary zygote clone mount-ID reorder.
replace_once(
    "fs/namespace.c",
    """#endif
\tmnt->mnt_parent = mnt;
\tlock_mount_hash();
""",
    """#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tif (likely(is_current_zygote_domain) && !(flag & CL_ZYGOTE_COPY_MNT_NS)) {
\t\tmnt->mnt.susfs_mnt_id_backup = mnt->mnt_id;
\t\tmnt->mnt_id = current->susfs_last_fake_mnt_id++;
\t}
#endif
\tmnt->mnt_parent = mnt;
\tlock_mount_hash();
""",
    "CL_ZYGOTE_COPY_MNT_NS))",
)

# copy_mnt_ns(): mark copy_tree() clones and tell clone_mnt() whether this is
# a zygote namespace copy or a regular namespace copy.
replace_once(
    "fs/namespace.c",
    """\tif (user_ns != ns->user_ns)
\t\tcopy_flags |= CL_SHARED_TO_SLAVE | CL_UNPRIVILEGED;
#ifdef CONFIG_KDP_NS
""",
    """\tif (user_ns != ns->user_ns)
\t\tcopy_flags |= CL_SHARED_TO_SLAVE | CL_UNPRIVILEGED;
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tcopy_flags |= CL_COPY_MNT_NS;
\tif (is_zygote_pid)
\t\tcopy_flags |= CL_ZYGOTE_COPY_MNT_NS;
#endif
#ifdef CONFIG_KDP_NS
""",
    "copy_flags |= CL_COPY_MNT_NS",
)

# Finish zygote fake-ID bookkeeping after the copied mount tree is complete.
replace_once(
    "fs/namespace.c",
    """#endif
\t\t\tp = next_mnt(p, old);
\t}
\tnamespace_unlock();
""",
    """#endif
\t\t\tp = next_mnt(p, old);
\t}
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tif (is_zygote_pid) {
\t\tlast_entry_mnt_id = list_first_entry(&new_ns->list, struct mount, mnt_list)->mnt_id;
\t\tlist_for_each_entry(q, &new_ns->list, mnt_list) {
\t\t\tif (unlikely(q->mnt_id >= DEFAULT_SUS_MNT_ID))
\t\t\t\tcontinue;
\t\t\tq->mnt.susfs_mnt_id_backup = q->mnt_id;
\t\t\tq->mnt_id = last_entry_mnt_id++;
\t\t}
\t}
\tcurrent->susfs_last_fake_mnt_id = last_entry_mnt_id;
#endif
\tnamespace_unlock();
""",
    "current->susfs_last_fake_mnt_id = last_entry_mnt_id",
)

# task_mmu.c in this MTK tree has a different include list from vanilla 4.19.
insert_after_once(
    "fs/proc/task_mmu.c",
    "#include <linux/uaccess.h>\n",
    "#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT\n#include <linux/susfs_def.h>\n#endif\n",
    "#include <linux/susfs_def.h>",
)

# Preserve Samsung's Android KABI layout while using reserved task_struct
# slots for the two SUSFS per-task values.
replace_once(
    "include/linux/sched.h",
    """#ifdef __GENKSYMS__
\tANDROID_KABI_RESERVE(3);
\tANDROID_KABI_RESERVE(4);
\tANDROID_KABI_RESERVE(5);
\tANDROID_KABI_RESERVE(6);
#else
\tstruct mutex\t\t\tfutex_exit_mutex;
#endif

\tANDROID_KABI_RESERVE(7);
\tANDROID_KABI_RESERVE(8);
""",
    """#ifdef __GENKSYMS__
\tANDROID_KABI_RESERVE(3);
\tANDROID_KABI_RESERVE(4);
\tANDROID_KABI_RESERVE(5);
#ifdef CONFIG_KSU_SUSFS
\tANDROID_KABI_USE(6, u64 susfs_task_state);
#else
\tANDROID_KABI_RESERVE(6);
#endif
#else
#ifdef CONFIG_KSU_SUSFS
\tu64 susfs_task_state;
#endif
\tstruct mutex\t\t\tfutex_exit_mutex;
#endif

\tANDROID_KABI_RESERVE(7);
#ifdef CONFIG_KSU_SUSFS
\tANDROID_KABI_USE(8, u64 susfs_last_fake_mnt_id);
#else
\tANDROID_KABI_RESERVE(8);
#endif
""",
    "susfs_last_fake_mnt_id",
)

# The selected A13VE defconfig does not enable KDP_NS. Fail early if a later
# config change enables it without the pointer-layout port being reviewed.
defconfig = read("arch/arm64/configs/a13ve_defconfig")
if "CONFIG_KDP_NS=y" in defconfig:
    raise SystemExit("CONFIG_KDP_NS=y needs the KDP pointer-layout SUSFS port; refusing an unsafe build")

# Final invariants: source files, mount hooks, and Kconfig-facing symbols must
# all be present before the build is allowed to continue.
checks = {
    "fs/Makefile": ["obj-$(CONFIG_KSU_SUSFS) += susfs.o"],
    "fs/dcache.c": ["#include <linux/susfs_def.h>"],
    "fs/namei.c": ["INODE_STATE_SUS_PATH"],
    "fs/namespace.c": ["susfs_mnt_alloc_id", "susfs_vfs_create_mount_done:", "CL_COPY_MNT_NS", "current->susfs_last_fake_mnt_id"],
    "fs/proc/task_mmu.c": ["#include <linux/susfs_def.h>"],
    "include/linux/mount.h": ["susfs_mnt_id_backup"],
    "include/linux/sched.h": ["susfs_task_state", "susfs_last_fake_mnt_id"],
    "fs/susfs.c": ["SUSFS"],
    "include/linux/susfs.h": ['SUSFS_VERSION "v1.5.5"'],
}
for rel, markers in checks.items():
    data = read(rel)
    for marker in markers:
        if marker not in data:
            raise SystemExit(f"{rel}: required SUSFS marker missing: {marker}")

print("[+] Samsung fs_context SUSFS v1.5.5 fixups applied and verified")
