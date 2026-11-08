"""Own one worker process tree and identify task owners without signalling PIDs."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _process_stamp(pid):
    """Return (alive / unknown, creation stamp); never send a signal to inspect a PID."""
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
        return None, None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return (False, None) if ctypes.get_last_error() == 87 else (None, None)
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return False, None
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None, None
            stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return True, str(stamp)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return False, None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return True, f"{boot}:{fields[19]}"
        except FileNotFoundError:
            return False, None
        except (OSError, ValueError, IndexError):
            return None, None
    return None, None


def process_owner(pid=None):
    pid = os.getpid() if pid is None else pid
    alive, stamp = _process_stamp(pid)
    return {"pid": pid, "creation_time": stamp} if alive and stamp else None


def owner_is_alive(owner):
    """True: same process. False: exited/reused PID. None: cannot safely establish it."""
    if not isinstance(owner, dict) or not isinstance(owner.get("creation_time"), str):
        return None
    alive, stamp = _process_stamp(owner.get("pid"))
    if alive is False:
        return False
    if alive is None or stamp is None:
        return None
    return stamp == owner["creation_time"]


class _WindowsJob:
    """Unnamed, non-inheritable job; closing it ends only the assigned tree."""
    def __init__(self):
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                        (name, wintypes.DWORD) for name in
                        ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
        self._accounting = Accounting
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                        wintypes.DWORD, ctypes.c_void_p]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, proc):
        # Popen's handle refers to this exact process, avoiding a PID-reuse race.
        if not self.kernel.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _open_member_handles(self):
        """Pin exact current members before termination removes them from the job list."""
        from ctypes import wintypes
        capacity = 16
        while True:
            class ProcessIds(ctypes.Structure):
                _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                            ("NumberOfProcessIdsInList", wintypes.DWORD),
                            ("ProcessIdList", ctypes.c_size_t * capacity)]
            listing = ProcessIds()
            if self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(listing),
                                                     ctypes.sizeof(listing), None):
                break
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA: the process list grew after sizing.
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, listing.NumberOfAssignedProcesses)
        handles = []
        try:
            for pid in listing.ProcessIdList[:listing.NumberOfProcessIdsInList]:
                handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:  # It exited before OpenProcess; no object remains to wait for.
                        continue
                    raise ctypes.WinError(error)
                member = wintypes.BOOL()
                if not self.kernel.IsProcessInJob(handle, self.handle, ctypes.byref(member)):
                    error = ctypes.WinError(ctypes.get_last_error())
                    self.kernel.CloseHandle(handle)
                    raise error
                if member.value:
                    handles.append(handle)
                else:
                    # A recycled PID may refer to an unrelated process; never wait on it.
                    self.kernel.CloseHandle(handle)
            return handles
        except BaseException:
            for handle in handles:
                self.kernel.CloseHandle(handle)
            raise

    def terminate_and_wait(self, timeout=5):
        deadline = time.monotonic() + timeout
        members = self._open_member_handles()
        try:
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # Include any child created between the initial snapshot and termination.
            members.extend(self._open_member_handles())
            while True:
                counts = self._accounting()
                if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(counts),
                                                             ctypes.sizeof(counts), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if counts.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("未能确认本任务的所有计算进程已退出。")
                time.sleep(.02)
            # Job accounting can reach zero before the individual process objects
            # are signalled. Waiting on pinned handles also closes that teardown race
            # when the bootstrap has already exited and Popen.wait() returns at once.
            for handle in members:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                result = self.kernel.WaitForSingleObject(handle, remaining_ms)
                if result == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result != 0:
                    raise RuntimeError("未能确认本任务的所有计算进程已退出。")
        finally:
            for handle in members:
                self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class OwnedProcess:
    def __init__(self, proc, job=None):
        self.process, self._job = proc, job
        self._closed = False

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        return self.process.poll()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            job, self._job = self._job, None
            try:
                # Also reap descendants after the bootstrap has already exited.
                job.terminate_and_wait()
                self.process.wait(timeout=5)
            finally:
                job.close()
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def start_owned_process(command, **kwargs):
    """Do not let a worker start until its bootstrap belongs to our Windows job."""
    if os.name != "nt":
        return OwnedProcess(subprocess.Popen(command, start_new_session=True, **kwargs))
    job = _WindowsJob()
    proc = None
    try:
        # The base interpreter starts directly, unlike a venv redirector that can
        # spawn a runtime child before AssignProcessToJobObject has run.
        executable = Path(getattr(sys, "_base_executable", sys.executable))
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        bootstrap = [str(executable), "-I", "-S", str(Path(__file__).resolve()),
                     "--owned-worker", *map(str, command)]
        proc = subprocess.Popen(bootstrap, stdin=subprocess.PIPE, **kwargs)
        job.assign(proc)
        proc.stdin.write(b"start\n")
        proc.stdin.flush()
        proc.stdin.close()
        return OwnedProcess(proc, job)
    except BaseException:
        # Before the gate opens the bootstrap cannot create a worker. Closing a
        # successfully assigned job additionally ends any released descendants.
        job.close()
        if proc is not None:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
        raise


def _bootstrap():
    if sys.stdin.buffer.readline() != b"start\n":
        return 2
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, creationflags=flags)
    return proc.wait()


if __name__ == "__main__" and sys.argv[1:2] == ["--owned-worker"]:
    raise SystemExit(_bootstrap())
