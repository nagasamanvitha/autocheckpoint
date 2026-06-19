from datetime import datetime

def format_relative_time(dt: datetime) -> str:
    """Returns a string representing the relative time since dt."""
    now = datetime.now()
    diff = now - dt
    
    seconds = diff.total_seconds()
    if seconds < 0:
        return "in the future"
    elif seconds < 60:
        return "just now"
    
    minutes = seconds // 60
    if minutes < 60:
        return f"{int(minutes)} minute{'s' if minutes > 1 else ''} ago"
        
    hours = minutes // 60
    if hours < 24:
        return f"{int(hours)} hour{'s' if hours > 1 else ''} ago"
        
    days = hours // 24
    return f"{int(days)} day{'s' if days > 1 else ''} ago"

import os

def is_pid_running(pid: int) -> bool:
    """Check if process with pid is running."""
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            is_running = (exit_code.value == 259) # STILL_ACTIVE = 259
            ctypes.windll.kernel32.CloseHandle(handle)
            return is_running
        return False
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True
