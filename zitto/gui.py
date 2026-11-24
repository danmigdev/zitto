"""The window: the songs on the left, the player on the right."""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import jobs, library, plugins, settings, theme
from .mixer import PLAYING
from .models import NAMES
from .player import PlayerView, all_peaks
from .source import LIST, SONG_IN_LIST, SourceError, is_url, kind, plain
from .surface import keep_content_on_resize
from .theme import ACCENT, BAD, BG, BUTTON, DIM, MAC, PANEL, TEXT, font
from .worker import BLOCKED, BUSY, DONE

AUDIO_TYPES = [("Audio and video", "*.mp3 *.wav *.flac *.m4a *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.webm"),
               ("All files", "*.*")]
SINGLES = "songs"  # row under which songs that are in no playlist are listed


def matches(words: list[str], *texts: str) -> bool:
    """Whether every word of a search (see `plain`) is somewhere in the texts."""
    text = plain(" ".join(texts))
    return all(word in text for word in words)


class App:
    def __init__(self, root: tk.Tk, source: str | None = None):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.work = jobs.Work(self.events)
        self.current: str | None = None  # song being prepared
        self.stage, self.percent = "", None
        self.links = plugins.links()  # where links can be opened from, besides files
        self.notice = " ".join(plugins.problems)  # shown when nothing is being prepared
        self.reading = 0  # playlists being read
        self.opening: str | None = None
        self.open_when_ready: str | None = None
        self.songs: dict[str, library.Song] = {}  # what is in the library, kept in memory
        self.lists: list[library.SavedPlaylist] = []
        self.keys: dict[str, str] = {}  # row -> song
        self.rows: dict[str, list[str]] = {}  # song -> rows
        self.groups: dict[str, list[str]] = {}  # group -> songs shown
        self._search_later: str | None = None
        self.k = theme.scale(root)

        theme.apply(root, self.k)
        root.title("zitto")
        root.geometry(f"{self.px(1400)}x{self.px(760)}")
        root.minsize(self.px(1120), self.px(560))
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if MAC:  # Quit, in the menu: without this it ends the program at once, leaving the worker behind
            root.createcommand("::tk::mac::Quit", self.close)
        self._build_list()
        self.player = PlayerView(root, self.k)
        self.player.grid(row=0, column=1, sticky="nsew")
        # Once it is on the screen: before that its outer frame does not exist.
        root.bind("<Map>", lambda e: keep_content_on_resize(root, self.player))
        self._bind_keys()
        self.reload()
        self._tick()

        last = settings.load().get("last_song")
        if source:
            self.add(source)
        elif last and (song := library.get(last)) and song.ready:
            self.open(last)
        self.start_work()

    def px(self, n: float) -> int:
        return round(n * self.k)

    # --- layout -----------------------------------------------------------

    def _build_list(self) -> None:
        side = tk.Frame(self.root, bg=PANEL, width=self.px(360))
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(4, weight=1)
        gap = self.px(12)

        what = ", ".join([s.label for s in self.links] + ["or files"]) if self.links else "audio or video files"
        tk.Label(side, text=f"Add {what}", bg=PANEL, fg=DIM,
                 font=font(9), anchor="w").grid(row=0, column=0, sticky="ew", padx=gap, pady=(gap, 4))
        if self.links:
            self.entry = tk.Entry(side, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                                  font=font(10), highlightthickness=1, highlightbackground=BUTTON,
                                  highlightcolor=ACCENT, selectbackground=ACCENT, selectforeground=BG)
            self.entry.grid(row=1, column=0, sticky="ew", padx=gap, ipady=self.px(5))
            self.entry.bind("<Return>", lambda e: self.add_entry())
        row = tk.Frame(side, bg=PANEL)
        row.grid(row=2, column=0, sticky="ew", padx=gap, pady=self.px(8))
        if self.links:
            theme.button(row, "Add link", self.add_entry, self.k).pack(side="left", padx=(0, self.px(6)))
        theme.button(row, "Files...", self.add_files, self.k).pack(side="left")
        theme.button(row, "Delete", self.delete_selected, self.k).pack(side="right")

        row = tk.Frame(side, bg=PANEL)
        row.grid(row=3, column=0, sticky="ew", padx=gap, pady=(0, self.px(8)))
        tk.Label(row, text="Search", bg=PANEL, fg=DIM, font=font(9)).pack(side="left", padx=(0, self.px(8)))
        self.query = tk.StringVar()
        self.search = tk.Entry(row, textvariable=self.query, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                               font=font(10), highlightthickness=1, highlightbackground=BUTTON,
                               highlightcolor=ACCENT, selectbackground=ACCENT, selectforeground=BG)
        self.search.pack(side="left", fill="x", expand=True, ipady=self.px(3))
        self.query.trace_add("write", lambda *a: self._search_soon())
        self.search.bind("<Return>", lambda e: self._open_first())
        self.search.bind("<Escape>", lambda e: self._clear_search())
        self.search.bind("<Down>", lambda e: self._into_list())

        box = tk.Frame(side, bg=PANEL)
        box.grid(row=4, column=0, sticky="nsew", padx=(gap, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, show="tree", columns=("state",), selectmode="extended", takefocus=0)
        self.tree.column("#0", width=self.px(265), stretch=True)
        self.tree.column("state", width=self.px(62), stretch=False, anchor="e")
        self.tree.tag_configure("todo", foreground=DIM)
        self.tree.tag_configure("failed", foreground=BAD)
        self.tree.tag_configure("open", foreground=ACCENT)
        self.tree.tag_configure("group", font=font(10, bold=True))
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview, takefocus=0)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<F2>", lambda e: self.rename_selected())
        # The right button of a Mac is 2 in Tk 8 and 3 in Tk 9.
        for click in ("<Button-2>", "<Button-3>", "<Control-Button-1>") if MAC else ("<Button-3>",):
            self.tree.bind(click, self._menu)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._explain())

        self.progress = ttk.Progressbar(side, maximum=100)
        self.progress.grid(row=5, column=0, sticky="ew", padx=gap, pady=(self.px(8), self.px(4)))
        self.status = tk.Label(side, bg=PANEL, fg=DIM, font=font(9), anchor="nw", justify="left",
                               height=3, wraplength=self.px(334))
        self.status.grid(row=6, column=0, sticky="ew", padx=gap, pady=(0, gap))

    def _bind_keys(self) -> None:
        player, command = self.player, "Command" if MAC else "Control"
        keys = {"<space>": lambda: player.do("toggle"), "<Home>": lambda: player.do("rewind"),
                "<Escape>": lambda: player.do("clear_loop"), "<Key-l>": player.toggle_loop,
                f"<{command}-s>": player.save}
        for i in range(len(NAMES)):
            keys[f"<Key-{i + 1}>"] = lambda i=i: player.toggle_main(i)
        for key, action in keys.items():
            self.root.bind(key, lambda e, action=action: None if isinstance(e.widget, tk.Entry) else action())
        self.root.bind(f"<{command}-f>", lambda e: (self.search.focus_set(), self.search.select_range(0, "end")))

    # --- list -------------------------------------------------------------

    def reload(self) -> None:
        """Read the whole library again."""
        self.songs = {s.key: s for s in library.songs()}
        self.lists = library.playlists()
        self.sync()

    def update_song(self, key: str) -> None:
        """Read one song again and refresh only its rows."""
        song = library.get(key)
        if song is None or key not in self.songs:
            self.reload()
            return
        self.songs[key] = song
        for row in self.rows.get(key, ()):
            text, state, tags = self._look(song)
            self.tree.item(row, text=text, values=(state,), tags=tags)
        for group, keys in self.groups.items():
            if key in keys:
                self.tree.set(group, "state", self._count(keys))
        self._show_status()

    def sync(self) -> None:
        """Make the list show what is known, or what the search finds in it."""
        words = plain(self.query.get()).split()
        groups = [(p.id, p.title, p.songs) for p in self.lists]
        listed = {k for p in self.lists for k in p.songs}
        groups.append((SINGLES, "Songs", [k for k, s in self.songs.items() if s.single or k not in listed]))

        chosen, opened = set(self.tree.selection()), {g: self.tree.item(g, "open") for g in self.tree.get_children()}
        self.tree.delete(*self.tree.get_children())
        self.keys, self.rows, self.groups = {}, {}, {}
        for group, title, keys in groups:
            found = [k for k in keys if k in self.songs and matches(words, self.songs[k].title, title)]
            if not found:
                continue
            self.groups[group] = found
            self.tree.insert("", "end", group, text=title, values=(self._count(found),), tags=("group",),
                             open=bool(words) or opened.get(group, True))
            for key in found:
                row = f"{group}/{key}"
                self.keys[row] = key
                self.rows.setdefault(key, []).append(row)
                text, state, tags = self._look(self.songs[key])
                self.tree.insert(group, "end", row, text=text, values=(state,), tags=tags)
        self.tree.selection_set([r for r in chosen if self.tree.exists(r)])
        self._show_status()

    def _count(self, keys: list[str]) -> str:
        ready = sum(self.songs[k].ready for k in keys)
        return str(len(keys)) if ready == len(keys) else f"{ready}/{len(keys)}"

    @property
    def left(self) -> int:
        """Songs still to be prepared."""
        return sum(not s.ready and not s.error for s in self.songs.values())

    # --- search -----------------------------------------------------------

    def _search_soon(self) -> None:
        """When the typing pauses, so that it never waits for the list."""
        if self._search_later:
            self.root.after_cancel(self._search_later)
        self._search_later = self.root.after(120, self._search_now)

    def _search_now(self) -> None:
        if self._search_later:
            self.root.after_cancel(self._search_later)
            self._search_later = None
        self.tree.yview_moveto(0)
        self.sync()

    def _clear_search(self) -> None:
        self.query.set("")
        self._search_now()
        self.player.wave.focus_set()

    def _open_first(self) -> None:
        self._search_now()
        if self.keys:
            self.open(next(iter(self.keys.values())))

    def _into_list(self) -> None:
        self._search_now()
        if self.keys:
            first = next(iter(self.keys))
            self.tree.focus_set()
            self.tree.focus(first)
            self.tree.selection_set(first)

    def _look(self, song: library.Song) -> tuple[str, str, tuple]:
        if song.ready:
            return song.title, "", ("open",) if song.key == self.player.key else ()
        if song.error:
            return song.title, "failed", ("failed",)
        if song.key == self.current:
            return song.title, "..." if self.percent is None else f"{self.percent}%", ("todo",)
        return song.title, "waiting", ("todo",)

    def _show_status(self) -> None:
        song = self.songs.get(self.current)
        if song:
            left = self.left
            more = f"\n{left - 1} more waiting" if left > 1 else ""
            self.status.configure(text=f"{self.stage or 'Starting'}: {song.title[:70]}{more}")
        elif self.reading:
            self.status.configure(text="Reading the playlist...")
        else:
            self.status.configure(text=self.notice)
        self.progress.configure(value=(self.percent or 0) if song else 0)

    def selected(self) -> list[str]:
        """Songs selected, directly or through their playlist."""
        rows = [r for s in self.tree.selection() for r in (s, *self.tree.get_children(s))]
        return list(dict.fromkeys(self.keys[r] for r in rows if r in self.keys))

    def _explain(self) -> None:
        keys = self.selected()
        song = self.songs.get(keys[0]) if len(keys) == 1 else None
        if song and song.error:
            self.say(song.error)

    def say(self, text: str) -> None:
        self.notice = text
        self._show_status()

    # --- adding -----------------------------------------------------------

    def add_entry(self) -> None:
        text = self.entry.get().strip()
        if text and self.add(text):
            self.entry.delete(0, "end")
            self.player.wave.focus_set()

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=AUDIO_TYPES)
        added = [k for k in (self._register(p) for p in paths) if k]
        self.reload()
        if len(added) == 1:
            self.open(added[0])
        self.start_work()

    def add(self, text: str) -> bool:
        """Add a file, or a link that a plugin opens. False when it was not accepted."""
        text = text.strip().strip('"')
        found = kind(text)
        whole = found == LIST
        if found == SONG_IN_LIST:
            whole = messagebox.askyesnocancel(
                "zitto", "This song is inside a list.\n\nYes: the whole list\nNo: only this song")
            if whole is None:
                return False
        if whole:
            self.reading += 1
            self._show_status()
            jobs.add_playlist(text, self.events)
            return True
        key = self._register(text)
        if key:
            self.reload()
            self.open(key)
            self.start_work()
        return bool(key)

    def _register(self, source: str) -> str | None:
        try:
            if not is_url(source) and not Path(source).expanduser().is_file():
                raise SourceError(f"File not found:\n{source}")
            return library.add(source).key
        except (SourceError, OSError) as e:
            messagebox.showerror("zitto", str(e))
            return None

    # --- opening ----------------------------------------------------------

    def _double_click(self, event) -> None:
        if self.tree.identify_row(event.y) in self.keys:
            self.open_selected()

    def open_selected(self) -> None:
        rows = [r for r in self.tree.selection() if r in self.keys]
        if rows:
            self.open(self.keys[rows[0]])

    def open(self, key: str) -> None:
        """Open a song, now or as soon as it is ready."""
        song = library.get(key)
        if not song:
            return
        if song.error:
            self.say(song.error)
        elif not song.ready:
            self.open_when_ready = key
            library.want(key)
            self.say("")
            self.player.say(f"Opens as soon as it is ready: {song.title}")
            self.start_work()
        else:
            self.open_when_ready, self.opening = None, key
            self.player.leave()
            threading.Thread(target=self._load, args=(song,), daemon=True).start()

    def _load(self, song: library.Song) -> None:
        try:
            stems = library.load_stems(song.key)
            self.events.put(("opened", (song, stems, all_peaks(stems))))
        except Exception as e:
            self.events.put(("not opened", (song, str(e) or type(e).__name__)))

    # --- deleting, retrying -----------------------------------------------

    def _menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        menu = tk.Menu(self.root, tearoff=0)
        if row in self.keys:
            menu.add_command(label="Open", command=self.open_selected)
            menu.add_command(label="Rename...", command=self.rename_selected)
        failed = [k for k in self.selected() if self.songs[k].error]
        if failed:
            menu.add_command(label="Try again", command=lambda: self.retry(failed))
        menu.add_command(label="Delete...", command=self.delete_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def retry(self, keys: list[str]) -> None:
        for key in keys:
            library.retry(key)
        self.say("")
        self.reload()
        self.start_work()

    def rename_selected(self) -> None:
        rows = [r for r in self.tree.selection() if r in self.keys]
        if not rows:
            return
        key = self.keys[rows[0]]
        name = simpledialog.askstring("zitto", "Name of the song (empty: its own name)", parent=self.root,
                                      initialvalue=self.songs[key].title)
        if name is not None:
            library.rename(key, name)
            self.update_song(key)
            if key == self.player.key:
                self.player.rename(self.songs[key].title)
                self.root.title(f"{self.songs[key].title} - zitto")

    def delete_selected(self) -> None:
        rows = self.tree.selection()
        lists = [r for r in rows if r not in self.keys and r != SINGLES]
        keys = [self.keys[r] for r in rows if r in self.keys]
        if SINGLES in rows:
            keys += [self.keys[r] for r in self.tree.get_children(SINGLES)]
        keys = list(dict.fromkeys(keys))
        if not lists and not keys:
            return
        total = sum(len(p.songs) for p in self.lists if p.id in lists)  # also those a search is hiding
        what = [f"{len(lists)} playlist{'s' * (len(lists) > 1)} with all {total} songs"] if lists else []
        what += [f"{len(keys)} song{'s' * (len(keys) > 1)}"] if keys else []
        if messagebox.askokcancel("zitto", f"Delete {' and '.join(what)} from this computer?\n\n"
                                              "Files you saved or added from your disk are not touched."):
            self.delete(lists, keys)

    def delete(self, lists: list[str], keys: list[str]) -> None:
        self.work.stop()  # it may be writing one of them
        self.current = None
        for list_id in lists:
            library.delete_playlist(list_id)
        for key in keys:
            library.delete(key)
        self.reload()
        self.start_work()

    # --- events -----------------------------------------------------------

    def start_work(self) -> None:
        if not self.work.running and self.left:
            self.work.start()

    def _tick(self) -> None:
        self.root.after(16, self._tick)  # first, so that an error below does not stop the window
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "work":
                self._work_event(value)
            elif kind == "added":
                self._playlist_added(value)
            elif kind == "opened":
                self._opened(*value)
            elif kind == "not opened":
                song, reason = value
                self.player.say(f"{song.title}: {reason}")
        self.player.refresh()

    def _work_event(self, data: dict) -> None:
        if data["run"] != self.work.run:
            return
        event = data["event"]
        if event == "song":
            self.current, self.stage, self.percent = data["key"], "", None
            self.update_song(self.current)
        elif event == "status":
            self.stage, self.percent = data["text"].rstrip("."), None
            self.update_song(self.current)
        elif event == "progress":
            self.percent = data["percent"]
            self._show_status()
            for row in self.rows.get(self.current, ()):
                self.tree.set(row, "state", f"{self.percent}%")
        elif event in ("ready", "failed"):
            key, self.current, self.percent = data["key"], None, None
            self.update_song(key)
            if key == self.open_when_ready:
                if self.player.mixer and self.player.mixer.state == PLAYING:  # do not cut the music
                    self.open_when_ready = None
                    self.player.say(f"Ready: {library.get(key).title}")
                else:
                    self.open(key)
        elif event == "blocked":
            self.say(f"{data['text']}. Try again later: what is missing continues at the next start.")
        elif event == "ended":
            self._work_ended(data["code"])

    def _work_ended(self, code: int) -> None:
        key, self.current = self.current, None
        self.work.ended()
        if code == BUSY:
            self.say("Another window is preparing the songs.")
            self.root.after(5000, self._after_busy)
        elif code not in (DONE, BLOCKED):
            # It crashed: blame the song it was on, so as not to crash on it for ever.
            if key:
                library.fail(key, "The separation stopped unexpectedly.")
            else:
                self.say("The preparation stopped unexpectedly.")
        self.reload()
        if code == DONE or (code != BUSY and code != BLOCKED and key):
            self.start_work()

    def _after_busy(self) -> None:
        self.say("")
        self.reload()
        self.start_work()

    def _playlist_added(self, data: dict) -> None:
        self.reading -= 1
        if data["event"] == "error":
            messagebox.showerror("zitto", data["text"])
        else:
            self.say(f"Added: {data['title']}, {data['count']} songs")
        self.reload()
        self.start_work()

    def _opened(self, song: library.Song, stems: library.StemSet, values: dict) -> None:
        if song.key != self.opening:
            return  # another one was chosen meanwhile
        self.opening, before = None, self.player.key
        self.player.open(song.key, song.title, stems, values)
        self.player.say("")
        self.root.title(f"{song.title} - zitto")
        settings.save(last_song=song.key)
        self.player.wave.focus_set()
        for key in (before, song.key):
            if key in self.songs:
                self.update_song(key)

    def close(self) -> None:
        self.work.stop()
        self.player.close()
        self.root.destroy()


def ensure_streams() -> None:
    """Under pythonw there is no console, and libraries that print would crash."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def main(source: str | None = None) -> int:
    ensure_streams()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp on scaled displays
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root, source)
    root.mainloop()
    return 0
