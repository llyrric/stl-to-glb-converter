"""
STL -> GLB Converter (desktop GUI)

A lightweight, cross-platform desktop app that batch-converts STL files into
indexed, non-decimated binary .glb files using a headless Blender process.

Run:
    python gui_app.py

Dependencies:
    pip install customtkinter   (optional; falls back to plain tkinter)
    Blender 3.x or 4.x must be installed somewhere on the system.
"""

import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BLENDER_SCRIPT = os.path.join(SCRIPT_DIR, "blender_convert.py")


# ---------------------------------------------------------------------------
# Persisted settings (so once Blender is located on a machine, either
# automatically or via manual Browse, it never needs to be searched for again)
# ---------------------------------------------------------------------------

def get_config_path():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "STLtoGLBConverter", "config.json")


def load_config():
    try:
        with open(get_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(data):
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Blender executable detection
# ---------------------------------------------------------------------------

def _resolve_windows_shortcut(lnk_path):
    """Resolve a .lnk shortcut's target path via the Windows Shell COM API."""
    try:
        escaped = lnk_path.replace("'", "''")
        script = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped}').TargetPath"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        target = result.stdout.strip()
        return target or None
    except Exception:
        return None


def _blender_exe_near(path):
    """Given any Blender-related exe path, return the sibling blender.exe if present."""
    if not path:
        return None
    directory = os.path.dirname(path)
    candidate = os.path.join(directory, "blender.exe")
    if os.path.isfile(candidate):
        return candidate
    if os.path.basename(path).lower() == "blender.exe" and os.path.isfile(path):
        return path
    return None


def _search_windows_registry():
    """Check Windows uninstall registry entries for a Blender InstallLocation."""
    try:
        import winreg
    except ImportError:
        return None

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    found = []
    for hive, subkey in roots:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                count = winreg.QueryInfoKey(root)[0]
                for i in range(count):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as entry:
                            display_name = winreg.QueryValueEx(entry, "DisplayName")[0]
                            if "blender" not in display_name.lower():
                                continue
                            install_location = winreg.QueryValueEx(entry, "InstallLocation")[0]
                            candidate = os.path.join(install_location, "blender.exe")
                            if os.path.isfile(candidate):
                                found.append((display_name, candidate))
                    except (OSError, FileNotFoundError, IndexError):
                        continue
        except OSError:
            continue
    if not found:
        return None
    found.sort(key=lambda pair: pair[0], reverse=True)  # prefer higher version strings
    return found[0][1]


def _search_windows_start_menu():
    """Walk Start Menu shortcuts for anything Blender-named and resolve its target."""
    search_roots = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
    ]
    lnk_files = []
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower().endswith(".lnk") and "blender" in fn.lower():
                    lnk_files.append(os.path.join(dirpath, fn))

    for lnk in lnk_files:
        target = _resolve_windows_shortcut(lnk)
        resolved = _blender_exe_near(target) if target else None
        if resolved:
            return resolved
    return None


def _search_windows_program_files():
    patterns = [
        r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    candidates.sort(reverse=True)  # prefer newer version strings
    return candidates[0]


def _search_macos():
    default = "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.path.isfile(default):
        return default

    home_apps = os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender")
    if os.path.isfile(home_apps):
        return home_apps

    try:
        result = subprocess.run(
            ["mdfind", "-name", "Blender.app"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.endswith("Blender.app"):
                candidate = os.path.join(line, "Contents/MacOS/Blender")
                if os.path.isfile(candidate):
                    return candidate
    except Exception:
        pass
    return None


def _search_linux():
    for candidate in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",
                       "/var/lib/flatpak/exports/bin/org.blender.Blender",
                       os.path.expanduser("~/.local/share/flatpak/exports/bin/org.blender.Blender")):
        if os.path.isfile(candidate):
            return candidate

    for pattern in ("/opt/blender*/blender", "/opt/*/blender*/blender"):
        matches = glob.glob(pattern)
        if matches:
            matches.sort(reverse=True)
            return matches[0]
    return None


def find_blender_executable():
    """Best-effort search for a Blender executable on the current platform.

    Tries, in order: a previously remembered path, the system PATH, OS-specific
    install registries/locations, then a broader filesystem search.
    """
    config = load_config()
    remembered = config.get("blender_path")
    if is_valid_blender(remembered):
        return remembered

    on_path = shutil.which("blender")
    if on_path:
        return on_path

    if sys.platform.startswith("win"):
        for finder in (_search_windows_registry, _search_windows_program_files,
                       _search_windows_start_menu):
            found = finder()
            if found:
                return found

    elif sys.platform == "darwin":
        found = _search_macos()
        if found:
            return found

    else:  # linux / other unix
        found = _search_linux()
        if found:
            return found

    return ""


def is_valid_blender(path):
    return bool(path) and os.path.isfile(path)


# ---------------------------------------------------------------------------
# Conversion worker
# ---------------------------------------------------------------------------

class ConversionJob:
    def __init__(self, input_path, output_path):
        self.input_path = input_path
        self.output_path = output_path


def run_conversion(blender_exe, job, log_queue):
    cmd = [
        blender_exe,
        "-b",
        "-P", BLENDER_SCRIPT,
        "--",
        job.input_path,
        job.output_path,
    ]
    log_queue.put(("log", f"$ Converting: {os.path.basename(job.input_path)}"))

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_queue.put(("log", f"  ERROR: failed to launch Blender: {exc}"))
        return False, str(exc)

    output_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            output_lines.append(line)
            log_queue.put(("log", f"  {line}"))
    proc.wait()

    if proc.returncode != 0:
        return False, "\n".join(output_lines[-20:])

    if not os.path.isfile(job.output_path):
        return False, "Blender exited successfully but no output file was produced."

    return True, None


def conversion_worker(blender_exe, jobs, overwrite, log_queue, cancel_event):
    total = len(jobs)
    succeeded = 0
    failed = []

    for idx, job in enumerate(jobs, start=1):
        if cancel_event.is_set():
            log_queue.put(("log", "Cancelled by user."))
            break

        log_queue.put(("progress", (idx, total, os.path.basename(job.input_path))))

        if os.path.isfile(job.output_path) and not overwrite:
            log_queue.put(("log", f"Skipping (already exists): {job.output_path}"))
            succeeded += 1
            continue

        ok, err = run_conversion(blender_exe, job, log_queue)
        if ok:
            succeeded += 1
            log_queue.put(("log", f"  OK -> {job.output_path}"))
        else:
            failed.append((job.input_path, err))
            log_queue.put(("log", f"  FAILED: {job.input_path}"))

    log_queue.put(("done", (succeeded, failed, total)))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

if HAS_CTK:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    BaseApp = ctk.CTk
else:
    BaseApp = tk.Tk


class App(BaseApp):
    def __init__(self):
        super().__init__()
        self.title("STL to GLB Converter")
        self.geometry("760x640")
        self.minsize(640, 540)

        self.input_files = []
        self.output_dir = tk.StringVar()
        self.overwrite_var = tk.BooleanVar(value=False)
        self.blender_path_var = tk.StringVar(value=find_blender_executable())

        self.log_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread = None

        self._build_ui()
        self._poll_queue()

    # -- UI construction ----------------------------------------------------

    def _frame(self, parent, **kwargs):
        if HAS_CTK:
            return ctk.CTkFrame(parent, **kwargs)
        return ttk.Frame(parent, **kwargs)

    def _label(self, parent, text, **kwargs):
        if HAS_CTK:
            return ctk.CTkLabel(parent, text=text, **kwargs)
        return ttk.Label(parent, text=text, **kwargs)

    def _button(self, parent, text, command, **kwargs):
        if HAS_CTK:
            return ctk.CTkButton(parent, text=text, command=command, **kwargs)
        return ttk.Button(parent, text=text, command=command, **kwargs)

    def _entry(self, parent, textvariable, **kwargs):
        if HAS_CTK:
            return ctk.CTkEntry(parent, textvariable=textvariable, **kwargs)
        return ttk.Entry(parent, textvariable=textvariable, **kwargs)

    def _checkbox(self, parent, text, variable, **kwargs):
        if HAS_CTK:
            return ctk.CTkCheckBox(parent, text=text, variable=variable, **kwargs)
        return ttk.Checkbutton(parent, text=text, variable=variable, **kwargs)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}

        # --- Blender path row ---
        blender_frame = self._frame(self)
        blender_frame.pack(fill="x", **pad)

        self._label(blender_frame, "Blender Executable:").pack(side="left")
        self.blender_entry = self._entry(blender_frame, self.blender_path_var)
        self.blender_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.blender_entry.bind("<KeyRelease>", lambda e: self._update_blender_status())

        self._button(blender_frame, "Browse", self._browse_blender).pack(side="left", padx=(0, 8))
        self.blender_status_label = self._label(blender_frame, "")
        self.blender_status_label.pack(side="left")

        # --- Input files ---
        input_frame = self._frame(self)
        input_frame.pack(fill="both", expand=False, **pad)

        input_header = self._frame(input_frame)
        input_header.pack(fill="x")
        self._label(input_header, "Input STL File(s):").pack(side="left")
        self._button(input_header, "Select STL File(s)", self._select_files).pack(side="right")
        self._button(input_header, "Clear", self._clear_files).pack(side="right", padx=(0, 8))

        list_container = self._frame(input_frame)
        list_container.pack(fill="both", expand=True, pady=(6, 0))

        self.file_listbox = tk.Listbox(list_container, height=7, bg="#2b2b2b", fg="white",
                                        selectbackground="#1f6aa5", highlightthickness=0,
                                        borderwidth=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.file_listbox.yview)
        self.file_listbox.configure(yscrollcommand=scrollbar.set)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.file_count_label = self._label(input_frame, "0 files selected")
        self.file_count_label.pack(anchor="w", pady=(4, 0))

        # --- Output directory ---
        output_frame = self._frame(self)
        output_frame.pack(fill="x", **pad)

        self._label(output_frame, "Output Directory:").pack(side="left")
        self.output_entry = self._entry(output_frame, self.output_dir)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=8)
        self._button(output_frame, "Browse", self._browse_output_dir).pack(side="left")

        overwrite_frame = self._frame(self)
        overwrite_frame.pack(fill="x", padx=12)
        self._checkbox(overwrite_frame, "Overwrite existing files", self.overwrite_var).pack(side="left")

        # --- Convert button + progress ---
        action_frame = self._frame(self)
        action_frame.pack(fill="x", **pad)

        self.convert_button = self._button(action_frame, "Convert to GLB", self._start_conversion)
        self.convert_button.pack(side="left")

        self.cancel_button = self._button(action_frame, "Cancel", self._cancel_conversion)
        self.cancel_button.pack(side="left", padx=(8, 0))
        self.cancel_button.configure(state="disabled")

        progress_frame = self._frame(self)
        progress_frame.pack(fill="x", padx=12)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                             maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(4, 0))

        self.progress_label = self._label(progress_frame, "Idle")
        self.progress_label.pack(anchor="w", pady=(2, 0))

        # --- Log window ---
        log_frame = self._frame(self)
        log_frame.pack(fill="both", expand=True, **pad)
        self._label(log_frame, "Log:").pack(anchor="w")

        log_container = self._frame(log_frame)
        log_container.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_container, height=10, bg="#1e1e1e", fg="#d4d4d4",
                                 wrap="word", state="disabled", borderwidth=0, highlightthickness=0)
        log_scroll = ttk.Scrollbar(log_container, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self._update_blender_status()

    # -- Blender detection ----------------------------------------------------

    def _update_blender_status(self):
        path = self.blender_path_var.get()
        if is_valid_blender(path):
            self.blender_status_label.configure(text="\u2713 Found")
            config = load_config()
            if config.get("blender_path") != path:
                config["blender_path"] = path
                save_config(config)
        else:
            self.blender_status_label.configure(text="\u2717 Not found")

    def _browse_blender(self):
        filetypes = [("Executable", "*.exe")] if sys.platform.startswith("win") else [("All files", "*")]
        path = filedialog.askopenfilename(title="Select Blender Executable", filetypes=filetypes)
        if path:
            self.blender_path_var.set(path)
            self._update_blender_status()

    # -- File selection ----------------------------------------------------

    def _select_files(self):
        paths = filedialog.askopenfilenames(
            title="Select STL File(s)",
            filetypes=[("STL files", "*.stl"), ("All files", "*")],
        )
        if not paths:
            return
        for p in paths:
            if p not in self.input_files:
                self.input_files.append(p)
        self._refresh_file_list()

        if not self.output_dir.get() and self.input_files:
            self.output_dir.set(os.path.dirname(self.input_files[0]))

    def _clear_files(self):
        self.input_files = []
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.file_listbox.delete(0, tk.END)
        for p in self.input_files:
            self.file_listbox.insert(tk.END, p)
        self.file_count_label.configure(text=f"{len(self.input_files)} file(s) selected")

    def _browse_output_dir(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_dir.set(path)

    # -- Logging / progress ----------------------------------------------------

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    idx, total, name = payload
                    pct = (idx - 1) / total * 100 if total else 0
                    self.progress_var.set(pct)
                    self.progress_label.configure(text=f"Converting {idx} of {total}: {name}...")
                elif kind == "done":
                    self._on_conversion_done(*payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # -- Conversion lifecycle ----------------------------------------------------

    def _start_conversion(self):
        blender_exe = self.blender_path_var.get()
        if not is_valid_blender(blender_exe):
            messagebox.showerror("Blender Not Found",
                                  "Please locate a valid Blender executable before converting.")
            return

        if not self.input_files:
            messagebox.showerror("No Files Selected", "Please select at least one STL file.")
            return

        out_dir = self.output_dir.get().strip()
        if not out_dir:
            messagebox.showerror("No Output Directory", "Please choose an output directory.")
            return
        os.makedirs(out_dir, exist_ok=True)

        jobs = []
        for input_path in self.input_files:
            base = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(out_dir, base + ".glb")
            jobs.append(ConversionJob(input_path, output_path))

        self.cancel_event = threading.Event()
        self.convert_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_var.set(0)
        self.progress_label.configure(text=f"Starting conversion of {len(jobs)} file(s)...")

        self.worker_thread = threading.Thread(
            target=conversion_worker,
            args=(blender_exe, jobs, self.overwrite_var.get(), self.log_queue, self.cancel_event),
            daemon=True,
        )
        self.worker_thread.start()

    def _cancel_conversion(self):
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")

    def _on_conversion_done(self, succeeded, failed, total):
        self.progress_var.set(100)
        self.convert_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

        if failed:
            self.progress_label.configure(text=f"Done: {succeeded}/{total} succeeded, {len(failed)} failed.")
            details = "\n".join(f"- {os.path.basename(p)}" for p, _ in failed[:10])
            messagebox.showwarning(
                "Conversion Complete with Errors",
                f"{succeeded} of {total} file(s) converted successfully.\n\n"
                f"Failed:\n{details}\n\nSee the log window for details.",
            )
        else:
            self.progress_label.configure(text=f"Done: {succeeded}/{total} succeeded.")
            messagebox.showinfo("Conversion Complete",
                                 f"All {succeeded} file(s) converted successfully.")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
