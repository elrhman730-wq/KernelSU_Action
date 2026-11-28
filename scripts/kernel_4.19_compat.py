#!/usr/bin/env python3
"""Backport small API compatibility fixes for the Samsung Linux 4.19.325 tree."""
from pathlib import Path
import sys
import re

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


def wlan_cmd_buf_unused_tc(s):
    # prTc is read only by DBGLOG; logging-off builds compile that call away.
    old = "struct TX_TCQ_STATUS *prTc ="
    new = "struct TX_TCQ_STATUS *prTc __maybe_unused ="
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("cmd_buf.c: prTc unused-variable annotation failed")
    if new not in s:
        raise SystemExit("cmd_buf.c: expected prTc declaration not found")
    return s


def wlan_gl_kal_unused_debug_vars(s):
    # These values are consumed only by debug logging macros. Keep the
    # calculations/data available while allowing logging-disabled builds.
    replacements = {
        "uint8_t ucCurEcoVer = wlanGetEcoVersion(": "uint8_t ucCurEcoVer __maybe_unused = wlanGetEcoVersion(",
        "char *pucBssSubType =": "char *pucBssSubType __maybe_unused =",
        "struct WIFI_LINK_QUALITY_INFO *lq =": "struct WIFI_LINK_QUALITY_INFO *lq __maybe_unused =",
    }
    for old, new in replacements.items():
        if old in s:
            s = s.replace(old, new, 1)
        if old in s:
            raise SystemExit("gl_kal.c: unused debug variable annotation failed: " + old)
        if new not in s:
            raise SystemExit("gl_kal.c: expected declaration not found: " + old)
    return s


def wlan_gl_wext_unused_debug_names(s):
    # These protocol/family labels are referenced only by DBGLOG. Preserve
    # the diagnostic strings while supporting builds with WLAN logging off.
    replacements = {
        'int8_t *aucIp[2] = {"IPv4", "IPv6"};':
            'int8_t *aucIp[2] __maybe_unused = {"IPv4", "IPv6"};',
        'int8_t *aucProto[2] = {"UDP", "TCP"};':
            'int8_t *aucProto[2] __maybe_unused = {"UDP", "TCP"};',
    }
    for old, new in replacements.items():
        if old in s:
            s = s.replace(old, new, 1)
        if old in s:
            raise SystemExit("gl_wext_priv.c: unused debug label annotation failed: " + old)
        if new not in s:
            raise SystemExit("gl_wext_priv.c: expected debug label declaration not found: " + old)
    return s


def imgsensor_s5k4hayx_void_prototype(s):
    # Clang treats an empty parameter list as an old-style, unspecified
    # prototype under -Wstrict-prototypes. This function takes no arguments.
    old = "void s5k4hayx_otp_off_setting()"
    new = "void s5k4hayx_otp_off_setting(void)"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("s5k4hayxmipiraw_Sensor.c: prototype correction failed")
    if new not in s:
        raise SystemExit("s5k4hayxmipiraw_Sensor.c: expected OTP function definition not found")
    return s


def wlan_gl_rst_unused_result(s):
    # fgResult only fed a DBGLOG statement. Keep reset callbacks and their
    # side effects, but explicitly discard their unused return values.
    start = s.find("static void mtk_wifi_reset_main(")
    end = s.find("\nstatic void mtk_wifi_reset(", start)
    if start < 0 or end < 0:
        raise SystemExit("gl_rst.c: cannot isolate mtk_wifi_reset_main")
    fn = s[start:end]
    fn = fn.replace("\tu_int8_t fgResult = FALSE;\n", "", 1)
    fn = fn.replace("\tfgResult = rst_L0_notify_step1();",
                    "\t(void)rst_L0_notify_step1();", 1)
    fn = fn.replace("\tfgResult = rst->prGlueInfo->prAdapter->chip_info->rst_L0_notify_step2();",
                    "\t(void)rst->prGlueInfo->prAdapter->chip_info->rst_L0_notify_step2();", 1)
    fn = fn.replace(r'DBGLOG(INIT, STATE, "[SER][L0] flow end, fgResult=%d\n", fgResult);',
                    r'DBGLOG(INIT, STATE, "[SER][L0] flow end\n");', 1)
    if "fgResult" in fn:
        raise SystemExit("gl_rst.c: reset result variable remains in reset function")
    if "(void)rst_L0_notify_step1();" not in fn or "(void)rst->prGlueInfo->prAdapter->chip_info->rst_L0_notify_step2();" not in fn:
        raise SystemExit("gl_rst.c: reset callback preservation check failed")
    return s[:start] + fn + s[end:]


def wlan_gl_cfg_scan_done_unused_status(s):
    # rStatus is a constant success value used only in the allocation-failure
    # debug message. The vendor source wraps DBGLOG arguments across lines.
    start = s.find("mtk_cfg80211_testmode_get_scan_done(")
    if start < 0:
        raise SystemExit("gl_cfg80211.c: scan-done function not found")
    end = s.find("\n}", start)
    if end < 0:
        raise SystemExit("gl_cfg80211.c: scan-done function end not found")
    fn = s[start:end]

    fn, decl_count = re.subn(
        r"^[ \t]*uint32_t rStatus = WLAN_STATUS_SUCCESS;[ \t]*\n",
        "",
        fn,
        count=1,
        flags=re.MULTILINE,
    )
    if decl_count == 0 and "uint32_t rStatus = WLAN_STATUS_SUCCESS;" in fn:
        raise SystemExit("gl_cfg80211.c: could not remove scan-done rStatus declaration")

    fn, log_count = re.subn(
        r'DBGLOG\(QM, TRACE,\s*"%s allocate skb failed:%x\\n",\s*__func__,\s*rStatus\);',
        r'DBGLOG(QM, TRACE, "%s allocate skb failed\\n", __func__);',
        fn,
        count=1,
    )
    if "rStatus" in fn:
        raise SystemExit("gl_cfg80211.c: scan-done logging-only status remains")
    if "allocate skb failed" not in fn:
        raise SystemExit("gl_cfg80211.c: allocation failure diagnostic was lost")
    return s[:start] + fn + s[end:]



def wlan_country_code_null_comparison(s):
    # u2CountryCode is a 16-bit numeric country code, not a pointer.
    # Comparing it with NULL triggers Clang's pointer-to-integer cast error.
    old = "if (prAdapter->rWifiVar.u2CountryCode != (uint16_t)NULL) {"
    new = "if (prAdapter->rWifiVar.u2CountryCode != 0) {"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("rlm_domain.c: invalid NULL comparison remains")
    if new not in s:
        raise SystemExit("rlm_domain.c: expected country-code comparison not found")
    return s

def wlan_remove_debug_disable_build_flag(s):
    # The upstream gen4m Makefile passes DBG_DISABLE_ALL_LOG=1 on the
    # compiler command line, while include/debug.h defines the same macro.
    # Remove the redundant command-line definition; debug.h is the single
    # source of truth and will set the driver's DBGLOG state to enabled.
    old = "ccflags-y += -DDBG_DISABLE_ALL_LOG=1"
    if old in s:
        s = s.replace(old, "# DBG_DISABLE_ALL_LOG is controlled by include/debug.h.", 1)
    if "-DDBG_DISABLE_ALL_LOG" in s:
        raise SystemExit("gen4m/Makefile: conflicting DBG_DISABLE_ALL_LOG compiler flag remains")
    if "DBG_DISABLE_ALL_LOG is controlled by include/debug.h." not in s:
        raise SystemExit("gen4m/Makefile: expected debug-disable compiler flag not found")
    return s


def wlan_enable_debug_logging(s):
    # MediaTek gen4m hard-disables every DBGLOG macro in this header.
    # Enable the driver's own runtime-filtered logging without globally
    # enabling unrelated kernel debug facilities.
    old = "#define DBG_DISABLE_ALL_LOG             1"
    new = "#define DBG_DISABLE_ALL_LOG             0"
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("WLAN debug.h: global DBGLOG disable remains")
    if new not in s:
        raise SystemExit("WLAN debug.h: expected DBG_DISABLE_ALL_LOG definition not found")
    return s

def wlan_gl_sys_macaddr_sscanf(s):
    # aucMacAddrOverride is an 18-byte array: 17 MAC characters plus NUL.
    # %18s may write 19 bytes including the terminator; %17s is the
    # correct bound and still accepts the complete colon-separated MAC.
    old = 'sscanf((uint8_t *)&aucMacAddrTemp, "%18s",'
    new = 'sscanf((uint8_t *)&aucMacAddrTemp, "%17s",'
    if old in s:
        s = s.replace(old, new, 1)
    if old in s:
        raise SystemExit("gl_sys.c: unsafe MAC address sscanf width remains")
    if new not in s:
        raise SystemExit("gl_sys.c: expected MAC address sscanf call not found")
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
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/nic/cmd_buf.c", wlan_cmd_buf_unused_tc)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/os/linux/gl_kal.c", wlan_gl_kal_unused_debug_vars)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/os/linux/gl_wext_priv.c", wlan_gl_wext_unused_debug_names)
edit("drivers/misc/mediatek/imgsensor/src/common/v1/s5k4hayx_mipi_raw/s5k4hayxmipiraw_Sensor.c", imgsensor_s5k4hayx_void_prototype)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/os/linux/gl_rst.c", wlan_gl_rst_unused_result)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/os/linux/gl_cfg80211.c", wlan_gl_cfg_scan_done_unused_status)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/os/linux/gl_sys.c", wlan_gl_sys_macaddr_sscanf)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/mgmt/rlm_domain.c", wlan_country_code_null_comparison)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/Makefile", wlan_remove_debug_disable_build_flag)
edit("drivers/misc/mediatek/connectivity/wlan/core/gen4m/include/debug.h", wlan_enable_debug_logging)

edit("include/linux/pid.h", pid_header)
edit("include/linux/fscrypt.h", fscrypt_header)
edit("mm/rmap.c", rmap_source)
edit("kernel/cred.c", cred_source)
print("[compat] Linux 4.19 API compatibility checks passed")

# Fresh build trigger: rerun A13VE KernelSU-Next Legacy + SUSFS validation.
