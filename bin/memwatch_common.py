"""Shared collection logic for TA-memwatch scripted inputs (meminfo/vmstat/slabinfo early warning)."""
import json
import os
import subprocess
import sys
import time

MEMINFO = "/proc/meminfo"
VMSTAT = "/proc/vmstat"
SLABINFO = "/proc/slabinfo"
STATE_FILE = "/var/tmp/ta_memwatch_state.json"
SAME_CYCLE_SECS = 60


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_meminfo():
    text = read_file(MEMINFO)
    info = {}
    if text:
        for line in text.splitlines():
            f = line.split()
            if len(f) < 2:
                continue
            v = _num(f[1])
            if v is not None:
                info[f[0].rstrip(":")] = v
    return info


def _parse_vmstat():
    text = read_file(VMSTAT)
    vals = {}
    if text:
        for line in text.splitlines():
            f = line.split()
            if len(f) < 2:
                continue
            if f[0] in ("pswpin", "pswpout", "oom_kill"):
                v = _num(f[1])
                if v is not None:
                    vals[f[0]] = int(v)
    return vals


def _read_slabinfo():
    text = read_file(SLABINFO)
    if text is not None:
        return text
    try:
        proc = subprocess.run(
            ["/usr/bin/sudo", "-n", "/bin/cat", SLABINFO],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write("TA-memwatch slabinfo sudo fallback failed: %s\n" % exc)
        return None
    if proc.returncode != 0:
        sys.stderr.write("TA-memwatch slabinfo sudo fallback failed: rc=%d %s\n"
                         % (proc.returncode, (proc.stderr or "").strip()))
        return None
    return proc.stdout


def _parse_slabinfo():
    text = _read_slabinfo()
    if text is None:
        return []
    entries = []
    for line in text.splitlines():
        if line.startswith("#") or line.startswith("slabinfo"):
            continue
        f = line.split()
        if len(f) < 4:
            continue
        active = _num(f[1])
        objsize = _num(f[3])
        if active is None or objsize is None:
            continue
        entries.append((f[0], int(active * objsize / 1024.0)))
    entries.sort(key=lambda e: e[1], reverse=True)
    return [{"name": n, "kb": kb} for n, kb in entries[:5]]


def _load_state():
    try:
        with open(STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state):
    tmp = "%s.%d.tmp" % (STATE_FILE, os.getpid())
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write("TA-memwatch state save failed: %s\n" % exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def discover():
    info = _parse_meminfo()
    total_kb = _num(info.get("MemTotal"))
    if total_kb is None or total_kb <= 0:
        return {"status": "absent"}
    data = {"mem_total_mb": int(total_kb / 1024.0)}
    avail_kb = _num(info.get("MemAvailable"))
    if avail_kb is not None:
        data["mem_available_mb"] = int(avail_kb / 1024.0)
        data["mem_available_pct"] = round(avail_kb / total_kb * 100.0, 2)
    swap_total = _num(info.get("SwapTotal"))
    swap_free = _num(info.get("SwapFree"))
    if swap_total is not None and swap_free is not None:
        if swap_total > 0:
            data["swap_used_pct"] = round((swap_total - swap_free) / swap_total * 100.0, 2)
        else:
            data["swap_used_pct"] = 0.0
    vm = _parse_vmstat()
    for key in ("pswpin", "pswpout", "oom_kill"):
        if key in vm:
            data[{"pswpin": "pswpin_total", "pswpout": "pswpout_total", "oom_kill": "oom_kill_total"}[key]] = vm[key]
    if "oom_kill_total" in data:
        state = _load_state()
        last_ts = _num(state.get("ts"))
        same_cycle = "oom_new" in state and last_ts is not None and (time.time() - last_ts) < SAME_CYCLE_SECS
        if same_cycle:
            d = _num(state.get("oom_new"))
            data["oom_new"] = max(0, int(d)) if d is not None else 0
        else:
            last = _num(state.get("oom_kill_total"))
            data["oom_new"] = max(0, data["oom_kill_total"] - int(last)) if last is not None else 0
            _save_state({"oom_kill_total": data["oom_kill_total"], "oom_new": data["oom_new"], "ts": time.time()})
    data["top_slabs"] = _parse_slabinfo()
    if data.get("oom_new", 0) > 0 or (data.get("swap_used_pct") or 0.0) > 90.0:
        data["status"] = "alarm"
    else:
        data["status"] = "ok"
    return data


def metrics_payload(data):
    if not isinstance(data, dict):
        data = {}
    out = {}
    int_keys = ("mem_total_mb", "pswpin_total", "pswpout_total", "oom_kill_total", "oom_new")
    for key in ("mem_total_mb", "mem_available_mb", "mem_available_pct", "swap_used_pct") + int_keys:
        v = _num(data.get(key))
        if v is None:
            continue
        out[key] = int(v) if key in int_keys else v
    slabs = data.get("top_slabs")
    if isinstance(slabs, list) and slabs and isinstance(slabs[0], dict):
        kb = _num(slabs[0].get("kb"))
        if kb is not None and kb >= 0:
            out["slab_top_kb"] = int(kb)
    return out


def emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def fail(message):
    sys.stderr.write("TA-memwatch collection error: %s\n" % message)
    return 1
