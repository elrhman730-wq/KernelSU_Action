#!/usr/bin/env python3
"""Integrate KernelSU-Next legacy manual hooks for this Samsung 4.19 tree."""
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
changes = {
    "fs/exec.c": [
        (
            "int do_execve(struct filename *filename,",
            "#ifdef CONFIG_KSU\nextern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,\n\t\t\t\tvoid *argv, void *envp, int *flags);\n#endif\n\nint do_execve(struct filename *filename,"
        ),
        (
            "\tstruct user_arg_ptr envp = { .ptr.native = __envp };\n\treturn do_execveat_common(AT_FDCWD, filename, argv, envp, 0);",
            "\tstruct user_arg_ptr envp = { .ptr.native = __envp };\n#ifdef CONFIG_KSU\n\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);\n#endif\n\treturn do_execveat_common(AT_FDCWD, filename, argv, envp, 0);"
        ),
        (
            "\tstruct user_arg_ptr envp = {\n\t\t.is_compat = true,\n\t\t.ptr.compat = __envp,\n\t};\n\treturn do_execveat_common(AT_FDCWD, filename, argv, envp, 0);",
            "\tstruct user_arg_ptr envp = {\n\t\t.is_compat = true,\n\t\t.ptr.compat = __envp,\n\t};\n#ifdef CONFIG_KSU\n\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);\n#endif\n\treturn do_execveat_common(AT_FDCWD, filename, argv, envp, 0);"
        ),
    ],
    "fs/open.c": [
        (
            "SYSCALL_DEFINE3(faccessat, int, dfd, const char __user *, filename, int, mode)\n{\n\treturn do_faccessat(dfd, filename, mode);",
            "#ifdef CONFIG_KSU\nextern int ksu_handle_faccessat(int *dfd, const char __user **filename_user, int *mode, int *flags);\n#endif\n\nSYSCALL_DEFINE3(faccessat, int, dfd, const char __user *, filename, int, mode)\n{\n#ifdef CONFIG_KSU\n\tksu_handle_faccessat(&dfd, &filename, &mode, NULL);\n#endif\n\treturn do_faccessat(dfd, filename, mode);"
        ),
    ],
    "fs/read_write.c": [
        (
            "SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)\n{\n\treturn ksys_read(fd, buf, count);",
            "#ifdef CONFIG_KSU\nextern int ksu_handle_sys_read(unsigned int fd, char __user **buf_ptr, size_t *count_ptr);\n#endif\n\nSYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)\n{\n#ifdef CONFIG_KSU\n\tksu_handle_sys_read(fd, &buf, &count);\n#endif\n\treturn ksys_read(fd, buf, count);"
        ),
    ],
    "fs/stat.c": [
        (
            "SYSCALL_DEFINE4(newfstatat, int, dfd, const char __user *, filename,\n\t\tstruct stat __user *, statbuf, int, flag)\n{\n\tstruct kstat stat;",
            "#ifdef CONFIG_KSU\nextern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n#endif\n\nSYSCALL_DEFINE4(newfstatat, int, dfd, const char __user *, filename,\n\t\tstruct stat __user *, statbuf, int, flag)\n{\n#ifdef CONFIG_KSU\n\tksu_handle_stat(&dfd, &filename, &flag);\n#endif\n\tstruct kstat stat;"
        ),
    ],
    "kernel/reboot.c": [
        (
            "SYSCALL_DEFINE4(reboot, int, magic1, int, magic2, unsigned int, cmd,\n\t\tvoid __user *, arg)\n{\n\tstruct pid_namespace *pid_ns = task_active_pid_ns(current);",
            "#ifdef CONFIG_KSU\nextern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg);\n#endif\n\nSYSCALL_DEFINE4(reboot, int, magic1, int, magic2, unsigned int, cmd,\n\t\tvoid __user *, arg)\n{\n#ifdef CONFIG_KSU\n\tksu_handle_sys_reboot(magic1, magic2, cmd, &arg);\n#endif\n\tstruct pid_namespace *pid_ns = task_active_pid_ns(current);"
        ),
    ],
}

for rel, pairs in changes.items():
    path = root / rel
    data = path.read_text()
    for old, new in pairs:
        marker = new.splitlines()[0]
        if marker in data and (rel != "fs/exec.c" or "ksu_handle_execveat" in data):
            continue
        if old not in data:
            # An already-integrated hook may have a different but valid placement.
            if rel == "fs/exec.c" and "ksu_handle_execveat" in data:
                continue
            if rel != "fs/exec.c" and {
                "fs/open.c": "ksu_handle_faccessat",
                "fs/read_write.c": "ksu_handle_sys_read",
                "fs/stat.c": "ksu_handle_stat",
                "kernel/reboot.c": "ksu_handle_sys_reboot",
            }[rel] in data:
                continue
            raise SystemExit(f"missing expected upstream 4.19 anchor in {rel}: {old[:100]!r}")
        data = data.replace(old, new, 1)
    path.write_text(data)

required = {
    "fs/exec.c": "ksu_handle_execveat",
    "fs/open.c": "ksu_handle_faccessat",
    "fs/read_write.c": "ksu_handle_sys_read",
    "fs/stat.c": "ksu_handle_stat",
    "kernel/reboot.c": "ksu_handle_sys_reboot",
}
for rel, symbol in required.items():
    data = (root / rel).read_text()
    if data.count(symbol) < (2 if rel == "fs/exec.c" else 2):
        raise SystemExit(f"verification failed: {symbol} missing or incomplete in {rel}")
print("[+] KernelSU-Next 4.19 manual hooks inserted and verified (exec, faccessat, read, stat, reboot)")
