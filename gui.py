"""
gui.py

Optional point-and-click front end for banana_tagger.py, built with
tkinter (ships with Python on Windows/macOS -- no extra install needed).

Includes a "Dry run" checkbox that previews matched/ambiguous/unmatched
names without creating a tag or writing anything to Mailchimp.

SETUP (one time):
  1. pip install -r requirements.txt
  2. Set MAILCHIMP_API_KEY, MAILCHIMP_SERVER, and MAILCHIMP_LIST_ID as
     environment variables (see README.md).
  3. Keep this file in the same folder as banana_tagger.py.

RUN:
  python gui.py

To ship a double-clickable app with no visible terminal (Windows):
  pip install pyinstaller
  pyinstaller --onefile --windowed --name "Banana Tagger" gui.py
This produces a standalone .exe in dist/. Mailchimp credentials still
need to be set as environment variables on whatever machine runs it.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import banana_tagger as tagger

REQUIRED_VARS = ("MAILCHIMP_API_KEY", "MAILCHIMP_SERVER", "MAILCHIMP_LIST_ID")


class TaggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Banana Tagger")
        self.geometry("640x520")
        self.minsize(560, 420)

        self.csv_path = tk.StringVar()
        self.tag_name = tk.StringVar()
        self.dry_run = tk.BooleanVar(value=False)
        self.log_queue = queue.Queue()
        self.worker = None

        self._build_layout()
        self._check_env_vars()
        self.after(100, self._poll_queue)

    def _build_layout(self):
        pad = {"padx": 12, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="Names CSV:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.csv_path, width=52).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(frm, text="Browse...", command=self._pick_csv).grid(row=0, column=2)

        ttk.Label(frm, text="Tag name:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.tag_name, width=52).grid(
            row=1, column=1, sticky="we", padx=6, pady=(8, 0)
        )

        frm.columnconfigure(1, weight=1)

        self.env_status = ttk.Label(self, text="", foreground="#555555")
        self.env_status.pack(fill="x", padx=12)

        ttk.Checkbutton(
            self,
            text="Dry run (preview matches, don't write anything to Mailchimp)",
            variable=self.dry_run,
            command=self._update_run_btn_label,
        ).pack(fill="x", padx=12, anchor="w")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=12, pady=8)
        self.run_btn = ttk.Button(btn_row, text="Run Tagging", command=self._start_run)
        self.run_btn.pack(side="left")
        ttk.Button(btn_row, text="Clear Output", command=self._clear_output).pack(side="left", padx=8)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=12, pady=(0, 8))

        out_frame = ttk.Frame(self)
        out_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.output = tk.Text(out_frame, wrap="word", state="disabled", bg="#f6f6f6")
        scroll = ttk.Scrollbar(out_frame, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _check_env_vars(self):
        missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
        if missing:
            self.env_status.configure(
                text=f"Missing environment variable(s): {', '.join(missing)}. "
                f"Set these and restart the app. See README.md.",
                foreground="#b00020",
            )
            self.run_btn.state(["disabled"])
        else:
            self.env_status.configure(text="Mailchimp credentials found.", foreground="#2a7050")

    def _update_run_btn_label(self):
        self.run_btn.configure(text="Preview (Dry Run)" if self.dry_run.get() else "Run Tagging")

    def _pick_csv(self):
        path = filedialog.askopenfilename(
            title="Select names CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.csv_path.set(path)

    def _clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        self.output.configure(state="disabled")

    def _append_output(self, text):
        self.output.configure(state="normal")
        self.output.insert(tk.END, text + "\n")
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def _start_run(self):
        csv_path = self.csv_path.get().strip()
        tag_name = self.tag_name.get().strip()
        if not csv_path or not tag_name:
            messagebox.showwarning("Missing info", "Please choose a CSV and enter a tag name.")
            return

        self._clear_output()
        self.run_btn.state(["disabled"])
        self.progress.start(12)

        self.worker = threading.Thread(
            target=self._run_worker, args=(csv_path, tag_name, self.dry_run.get()), daemon=True
        )
        self.worker.start()

    def _run_worker(self, csv_path, tag_name, dry_run):
        try:
            tagger.run_tagging(csv_path, tag_name, log=self.log_queue.put, dry_run=dry_run)
        except EnvironmentError as e:
            self.log_queue.put(f"Error: {e}")
        except FileNotFoundError:
            self.log_queue.put(f"Error: CSV file not found: {csv_path}")
        except ValueError as e:
            self.log_queue.put(f"Error: {e}")
        except tagger.requests.exceptions.RequestException as e:
            self.log_queue.put(f"Error: {tagger.describe_request_error(e)}")
        except Exception as e:  # catch-all so the GUI never hangs silently
            self.log_queue.put(f"Error: {e}")
        finally:
            self.log_queue.put(None)  # sentinel: run complete

    def _poll_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item is None:
                    self.progress.stop()
                    self.run_btn.state(["!disabled"])
                else:
                    self._append_output(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


def main():
    app = TaggerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
