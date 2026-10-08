# -*- coding: utf-8 -*-
"""歌单排序 —— 给姥爷的 U 盘歌曲一键编号工具。

打开 U 盘 → 列表排好 → 想动就拖 → 一键编号 → 所有歌变成 001-xxx、002-xxx。
"""
import json
import os
import re
import sys
import subprocess
from datetime import datetime

AUDIO_EXTS = {".mp3", ".wma", ".wav", ".flac", ".m4a", ".ape", ".aac", ".ogg"}
UNDO_FILE = "歌单排序_撤销.json"
NUM_PREFIX = re.compile(r"^\s*\d{1,4}\s*[-_.．、 ]\s*")


# ---------------- 纯逻辑，不碰界面 ----------------

def strip_number(name):
    """去掉旧编号前缀：'010-A邓丽君 - 四季歌' -> 'A邓丽君 - 四季歌'。"""
    return NUM_PREFIX.sub("", name, count=1).strip()


def leading_number(name):
    m = re.match(r"^\s*(\d{1,4})", name)
    return int(m.group(1)) if m else None


def pinyin_key(text):
    """中文按拼音排，没装 pypinyin 就按码点。"""
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(text)).lower()
    except Exception:
        return text.lower()


def singer_of(clean_name):
    """'A邓丽君 - 四季歌' -> '邓丽君'；没有 ' - ' 就返回整名。"""
    base = clean_name[1:] if is_marked(clean_name) else clean_name
    if " - " in base:
        return base.split(" - ", 1)[0].strip()
    if "-" in base:
        return base.split("-", 1)[0].strip()
    return base.strip()


def is_marked(clean_name):
    """姥爷手动加的 A 标记。"""
    return bool(re.match(r"^A(?![a-zA-Z])", clean_name))


def scan_folder(folder):
    """返回 [(原文件名, 去号后的名, 扩展名), ...]，按旧编号→名字排好。"""
    items = []
    for fn in os.listdir(folder):
        full = os.path.join(folder, fn)
        if not os.path.isfile(full):
            continue
        base, ext = os.path.splitext(fn)
        if ext.lower() not in AUDIO_EXTS:
            continue
        items.append((fn, strip_number(base), ext))
    items.sort(key=lambda t: (leading_number(t[0]) is None,
                              leading_number(t[0]) or 0,
                              t[1]))
    return items


def _body(clean_name):
    return clean_name[1:] if is_marked(clean_name) else clean_name


def sort_by_name(items):
    return sorted(items, key=lambda t: (not is_marked(t[1]), pinyin_key(_body(t[1]))))


def sort_by_singer(items):
    return sorted(items, key=lambda t: (not is_marked(t[1]),
                                        pinyin_key(singer_of(t[1])),
                                        pinyin_key(_body(t[1]))))


def plan_renames(items):
    """按当前顺序生成 (旧文件名, 新文件名) 列表。"""
    width = max(3, len(str(len(items))))
    plan = []
    for i, (old, clean, ext) in enumerate(items, 1):
        new = f"{i:0{width}d}-{clean}{ext}"
        if new != old:
            plan.append((old, new))
    return plan


def apply_renames(folder, plan):
    """两段改名，避免 001 和 002 互换时撞车。返回实际完成的 (旧, 新)。

    中途出错（比如有首歌正在被播放器占着）就把已经改过的全部改回原名再抛出，
    不让 U 盘上留下 ~0000~ 开头的半成品。
    """
    temps = []      # 已改成临时名的 (旧, 临时, 新)
    finals = []     # 已改成正式名的 (旧, 临时, 新)
    try:
        for old, new in plan:
            tmp = f"~{len(temps):04d}~{new}"
            os.rename(os.path.join(folder, old), os.path.join(folder, tmp))
            temps.append((old, tmp, new))
        for old, tmp, new in temps:
            os.rename(os.path.join(folder, tmp), os.path.join(folder, new))
            finals.append((old, tmp, new))
    except OSError:
        for old, tmp, new in reversed(finals):
            try:
                os.rename(os.path.join(folder, new), os.path.join(folder, tmp))
            except OSError:
                pass
        for old, tmp, new in reversed(temps):
            try:
                os.rename(os.path.join(folder, tmp), os.path.join(folder, old))
            except OSError:
                pass
        raise
    return [(old, new) for old, tmp, new in finals]


def save_undo(folder, done):
    with open(os.path.join(folder, UNDO_FILE), "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().isoformat(timespec="seconds"),
                   "renames": done}, f, ensure_ascii=False, indent=1)


def load_undo(folder):
    p = os.path.join(folder, UNDO_FILE)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def undo(folder):
    data = load_undo(folder)
    if not data:
        return 0
    plan = [(new, old) for old, new in data["renames"]
            if os.path.exists(os.path.join(folder, new))]
    apply_renames(folder, plan)
    os.remove(os.path.join(folder, UNDO_FILE))
    return len(plan)


def write_playlist(folder, items):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out = os.path.join(folder, f"歌单_{stamp}.txt")
    with open(out, "w", encoding="gbk", errors="replace") as f:
        for i, (old, clean, ext) in enumerate(items, 1):
            f.write(f"{i:03d}  {clean}\n")
    return out


def open_file(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def default_folder():
    for d in ("E:\\", "F:\\", "G:\\"):
        if os.path.isdir(d):
            return d
    return os.path.expanduser("~")


# ---------------- 界面 ----------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    FONT = ("Microsoft YaHei UI", 14)
    FONT_BIG = ("Microsoft YaHei UI", 15, "bold")
    FONT_SMALL = ("Microsoft YaHei UI", 11)

    root = tk.Tk()
    root.title("歌单排序")
    root.geometry("960x680")
    root.minsize(760, 520)

    state = {"folder": None, "items": [], "drag_from": None, "view": []}

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Treeview", font=FONT, rowheight=38)
    style.configure("Treeview.Heading", font=FONT)
    style.configure("TButton", font=FONT, padding=(14, 8))
    style.configure("Big.TButton", font=FONT_BIG, padding=(22, 10))
    style.configure("TLabel", font=FONT)
    style.configure("Hint.TLabel", font=FONT_SMALL, foreground="#666666")

    # 顶栏
    top = ttk.Frame(root, padding=(14, 12, 14, 6))
    top.pack(fill="x")
    folder_var = tk.StringVar(value="还没打开文件夹")
    ttk.Button(top, text="打开 U 盘 / 文件夹", command=lambda: choose_folder()).pack(side="left")
    ttk.Button(top, text="按名字排", command=lambda: resort(sort_by_name)).pack(side="left", padx=(10, 0))
    ttk.Button(top, text="按歌手排", command=lambda: resort(sort_by_singer)).pack(side="left", padx=(10, 0))
    ttk.Label(top, textvariable=folder_var, style="Hint.TLabel").pack(side="right")

    ttk.Label(root, text="按住一行可以上下拖；想放前面的，选中后点「置顶」。带 A 的是你自己标过的歌。",
              style="Hint.TLabel", padding=(14, 0, 14, 6)).pack(fill="x")

    # 搜索
    srch = ttk.Frame(root, padding=(14, 0, 14, 8))
    srch.pack(fill="x")
    ttk.Label(srch, text="找歌：").pack(side="left")
    search_var = tk.StringVar()
    search_entry = ttk.Entry(srch, textvariable=search_var, font=FONT, width=28)
    search_entry.pack(side="left", padx=(4, 8))
    ttk.Button(srch, text="清空", command=lambda: search_var.set("")).pack(side="left")
    match_var = tk.StringVar(value="")
    ttk.Label(srch, textvariable=match_var, style="Hint.TLabel").pack(side="left", padx=(12, 0))

    # 列表
    mid = ttk.Frame(root, padding=(14, 0, 14, 0))
    mid.pack(fill="both", expand=True)
    cols = ("num", "name")
    tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
    tree.heading("num", text="序号")
    tree.heading("name", text="歌名")
    tree.column("num", width=90, anchor="center", stretch=False)
    tree.column("name", anchor="w")
    tree.tag_configure("marked", foreground="#185FA5")
    vsb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    side = ttk.Frame(root, padding=(14, 8, 14, 0))
    side.pack(fill="x")
    ttk.Button(side, text="置顶", command=lambda: move_selected("top")).pack(side="left")
    ttk.Button(side, text="上移", command=lambda: move_selected(-1)).pack(side="left", padx=(10, 0))
    ttk.Button(side, text="下移", command=lambda: move_selected(1)).pack(side="left", padx=(10, 0))
    count_var = tk.StringVar(value="")
    ttk.Label(side, textvariable=count_var, style="Hint.TLabel").pack(side="right")

    # 底栏
    bottom = ttk.Frame(root, padding=(14, 10, 14, 14))
    bottom.pack(fill="x")
    ttk.Button(bottom, text="一键编号", style="Big.TButton", command=lambda: do_number()).pack(side="right")
    ttk.Button(bottom, text="撤销上次编号", command=lambda: do_undo()).pack(side="right", padx=(0, 12))
    ttk.Button(bottom, text="出歌单 txt", command=lambda: do_playlist()).pack(side="left")

    # ---- 函数 ----
    def filtering():
        return bool(search_var.get().strip())

    def refresh():
        tree.delete(*tree.get_children())
        q = search_var.get().strip().lower()
        view = []
        for i, (old, clean, ext) in enumerate(state["items"]):
            if q and q not in clean.lower() and q not in pinyin_key(clean):
                continue
            view.append(i)
            tags = ("marked",) if is_marked(clean) else ()
            tree.insert("", "end", iid=str(i), values=(f"{i + 1:03d}", clean), tags=tags)
        state["view"] = view
        count_var.set(f"共 {len(state['items'])} 首" if state["items"] else "")
        match_var.set(f"找到 {len(view)} 首" if q else "")

    search_var.trace_add("write", lambda *a: refresh())

    def load(folder):
        try:
            items = scan_folder(folder)
        except OSError as e:
            messagebox.showerror("打不开", f"这个文件夹打不开：\n{e}")
            return
        state["folder"] = folder
        state["items"] = items
        folder_var.set(folder)
        refresh()
        if not items:
            messagebox.showinfo("没有歌", "这个文件夹里没找到歌，看看是不是插错盘了。")

    def choose_folder():
        d = filedialog.askdirectory(initialdir=state["folder"] or default_folder(),
                                    title="选歌所在的文件夹（一般是 U 盘）")
        if d:
            load(os.path.normpath(d))

    def resort(fn):
        if not state["items"]:
            return
        state["items"] = fn(state["items"])
        refresh()

    def selected_index():
        sel = tree.selection()
        return int(sel[0]) if sel else None

    def move_selected(how):
        i = selected_index()
        items = state["items"]
        if i is None or not items:
            return
        if how == "top":
            j = 0
        else:
            j = i + how
            if j < 0 or j >= len(items):
                return
        it = items.pop(i)
        items.insert(j, it)
        refresh()
        if tree.exists(str(j)):
            tree.selection_set(str(j))
            tree.see(str(j))

    # 拖拽
    def on_press(e):
        if filtering():
            state["drag_from"] = None
            return
        row = tree.identify_row(e.y)
        state["drag_from"] = int(row) if row else None

    def on_motion(e):
        if state["drag_from"] is None:
            return
        row = tree.identify_row(e.y)
        if not row:
            return
        j = int(row)
        i = state["drag_from"]
        if j != i:
            it = state["items"].pop(i)
            state["items"].insert(j, it)
            state["drag_from"] = j
            refresh()
            tree.selection_set(str(j))

    def on_release(e):
        state["drag_from"] = None

    tree.bind("<ButtonPress-1>", on_press)
    tree.bind("<B1-Motion>", on_motion)
    tree.bind("<ButtonRelease-1>", on_release)

    def do_number():
        if not state["items"]:
            messagebox.showinfo("先打开", "先点左上角「打开 U 盘 / 文件夹」。")
            return
        plan = plan_renames(state["items"])
        if not plan:
            messagebox.showinfo("已经好了", "编号已经是对的，不用再改。")
            return
        if not messagebox.askyesno("确认编号",
                                   f"要把 {len(plan)} 首歌的文件名改成 001-、002- 这样吗？\n\n改错了可以点「撤销上次编号」退回。"):
            return
        try:
            done = apply_renames(state["folder"], plan)
            save_undo(state["folder"], done)
        except OSError as e:
            load(state["folder"])
            messagebox.showerror("没改成",
                                 f"有首歌可能正在播放，改不了名。已经全部退回原样，什么都没动。\n"
                                 f"先把播放器关掉，再点一次「一键编号」。\n\n{e}")
            return
        load(state["folder"])
        messagebox.showinfo("好了", f"已经给 {len(done)} 首歌编好号。")

    def do_undo():
        if not state["folder"]:
            return
        data = load_undo(state["folder"])
        if not data:
            messagebox.showinfo("没有可撤销的", "这个文件夹还没编过号，或者已经撤销过了。")
            return
        if not messagebox.askyesno("撤销", f"退回到 {data['time'][:16].replace('T', ' ')} 之前的文件名？"):
            return
        n = undo(state["folder"])
        load(state["folder"])
        messagebox.showinfo("已撤销", f"{n} 首歌改回去了。")

    def do_playlist():
        if not state["items"]:
            messagebox.showinfo("先打开", "先点左上角「打开 U 盘 / 文件夹」。")
            return
        out = write_playlist(state["folder"], state["items"])
        try:
            open_file(out)
        except OSError:
            messagebox.showinfo("歌单已保存", out)

    # 启动：E 盘有就直接读
    d = default_folder()
    if d.endswith(":\\"):
        root.after(100, lambda: load(d))

    root.mainloop()


if __name__ == "__main__":
    run_gui()
