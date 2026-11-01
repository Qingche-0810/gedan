# -*- coding: utf-8 -*-
"""歌单排序 —— 给家里老人的 U 盘歌曲一键编号工具。

打开 U 盘 → 列表排好 → 想动就拖 → 一键编号 → 所有歌变成 001-xxx、002-xxx，\n并把 U 盘目录里登记的先后也理成这个顺序（音响按登记先后放，不按文件名）。
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
    """老人手动加的 A 标记。"""
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


# ---------------- 目录顺序（音响按这个放，不按文件名） ----------------

STAGE_PREFIX = "~GEDAN"   # 大写、不超过 8 个字，在 U 盘上只占一格登记位


def _dir_order(folder, names):
    """按 U 盘目录里登记的先后，返回 names 里有的那些。"""
    want = set(names)
    return [n for n in os.listdir(folder) if n in want]


def _make_stage(folder, k):
    p = os.path.join(folder, f"{STAGE_PREFIX}{k}")
    os.mkdir(p)
    return p


def _front_stage(folder, stage):
    """让临时文件夹的登记位挪到最前面，这样搬回来的歌才会一个接一个排在它后面。

    探法：在这一层新建一个只占一格的小文件，看它登记在临时文件夹前面还是后面；
    在前面就说明前头还有空位，再建一个临时文件夹（会落到那个空位），把东西搬过去。
    """
    k = 1
    for _ in range(8):
        probe = os.path.join(folder, f"{STAGE_PREFIX}P")
        with open(probe, "w"):
            pass
        order = os.listdir(folder)
        before = order.index(os.path.basename(probe)) < order.index(os.path.basename(stage))
        os.remove(probe)
        if not before:
            return stage
        k += 1
        new = _make_stage(folder, k)
        for n in os.listdir(stage):
            os.rename(os.path.join(stage, n), os.path.join(new, n))
        os.rmdir(stage)
        stage = new
    return stage


def reorder_directory(folder, ordered_names):
    """把这一层目录的登记顺序理成 ordered_names 的顺序。返回理完以后顺序对不对。

    很多音响、车机不看文件名，按目录里登记的先后放歌，也就是拷进 U 盘的先后。
    改名不动登记顺序，所以文件名排成 001、002 了，放出来还是老顺序。
    办法：把这一层的东西全搬进一个临时文件夹（同一个盘里搬只改登记、不拷数据，一瞬间），
    再按顺序一个个搬回来。歌在前，其它文件跟在后面。搬不动的（系统文件夹）就留在原地。
    中途出错，临时文件夹里的东西全部搬回原处再抛出。
    """
    ordered_names = list(ordered_names)
    stage = None
    try:
        stage = _make_stage(folder, 1)
        moved = []
        for n in os.listdir(folder):
            if n.startswith(STAGE_PREFIX):
                continue
            try:
                os.rename(os.path.join(folder, n), os.path.join(stage, n))
                moved.append(n)
            except OSError:
                pass            # 搬不动的留在原地
        stage = _front_stage(folder, stage)
        in_order = [n for n in ordered_names if n in set(moved)]
        rest = [n for n in moved if n not in set(in_order)]
        for n in in_order + rest:
            os.rename(os.path.join(stage, n), os.path.join(folder, n))
        os.rmdir(stage)
        stage = None
    finally:
        if stage and os.path.isdir(stage):
            for n in os.listdir(stage):
                try:
                    os.rename(os.path.join(stage, n), os.path.join(folder, n))
                except OSError:
                    pass
            try:
                os.rmdir(stage)
            except OSError:
                pass
    return _dir_order(folder, ordered_names) == ordered_names


SETTINGS_FILE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "歌单排序", "设置.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(d):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def desktop_dir():
    """桌面。Windows 问系统要真正的桌面位置（桌面被挪到别的盘、OneDrive 接管都认得出），要不到就用 ~/Desktop。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and os.path.isdir(buf.value):
                return buf.value
        except Exception:
            pass
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def playlist_dir(settings):
    """歌单存哪：设置里钉过的那个目录；没钉过、或者那个目录没了，就是桌面。"""
    d = settings.get("playlist_dir")
    if d and os.path.isdir(d):
        return d
    return desktop_dir()


def write_playlist(out_dir, items):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out = os.path.join(out_dir, f"歌单_{stamp}.txt")
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

    # 系统缩放 125% / 150% / 175% 的电脑：不声明的话 Windows 会把整个窗口当图片放大，
    # 960x680 放到 175% 就是 1680x1190，比屏幕还高，最下面一排按钮直接被吃掉（姥爷那台就是这样）。
    # 声明了，Tk 就按真实 DPI 画，字号自己跟着缩放走，窗口大小我们再按屏幕封顶。
    if sys.platform.startswith("win"):
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.title("歌单排序")

    # scale：1.0 = 100%，1.75 = 175%。字号本来就是按老人眼睛挑大的，缩放再乘一遍就太大了——
    # 超过 140% 的部分字号往回收一点（175% 的屏上看着跟 140% 一样大），行高和按钮边距跟着字走。
    try:
        scale = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
    except Exception:
        scale = 1.0
    font_k = min(scale, 1.4) / scale          # 字号系数（点数本身已被 Tk 按 DPI 放大）
    px_k = min(scale, 1.4)                    # 像素尺寸系数（行高、边距）

    def pt(n):
        return max(8, round(n * font_k))

    FONT = ("Microsoft YaHei UI", pt(14))
    FONT_BIG = ("Microsoft YaHei UI", pt(15), "bold")
    FONT_SMALL = ("Microsoft YaHei UI", pt(11))

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = min(round(960 * px_k), sw - 40)
    h = min(round(680 * px_k), sh - 120)      # 留出任务栏和标题栏
    root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 30)}")
    root.minsize(min(round(640 * px_k), w), min(round(420 * px_k), h))

    state = {"folder": None, "items": [], "drag_from": None, "view": [], "settings": load_settings()}

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Treeview", font=FONT, rowheight=round(38 * px_k))
    style.configure("Treeview.Heading", font=FONT)
    style.configure("TButton", font=FONT, padding=(round(14 * px_k), round(8 * px_k)))
    style.configure("Big.TButton", font=FONT_BIG, padding=(round(22 * px_k), round(10 * px_k)))
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

    # 底栏（先于列表 pack 到底部：窗口不够高的时候缩的是列表，不是这排按钮）
    bottom = ttk.Frame(root, padding=(14, 10, 14, 14))
    bottom.pack(side="bottom", fill="x")
    ttk.Button(bottom, text="出歌单 txt", command=lambda: do_playlist()).pack(side="left")
    ttk.Button(bottom, text="一键编号", style="Big.TButton", command=lambda: do_number()).pack(side="left", padx=(12, 0))
    ttk.Button(bottom, text="撤销上次编号", command=lambda: do_undo()).pack(side="right")
    where_var = tk.StringVar()
    ttk.Button(bottom, textvariable=where_var, command=lambda: choose_playlist_dir()).pack(side="right", padx=(0, 12))

    side = ttk.Frame(root, padding=(14, 8, 14, 0))
    side.pack(side="bottom", fill="x")
    ttk.Button(side, text="置顶", command=lambda: move_selected("top")).pack(side="left")
    ttk.Button(side, text="上移", command=lambda: move_selected(-1)).pack(side="left", padx=(10, 0))
    ttk.Button(side, text="下移", command=lambda: move_selected(1)).pack(side="left", padx=(10, 0))
    count_var = tk.StringVar(value="")
    ttk.Label(side, textvariable=count_var, style="Hint.TLabel").pack(side="right")

    # 列表
    mid = ttk.Frame(root, padding=(14, 0, 14, 0))
    mid.pack(side="top", fill="both", expand=True)
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
            if not messagebox.askyesno("编号已经是对的",
                                       "文件名已经是 001-、002- 排好的。\n\n"
                                       "要不要把 U 盘里的播放顺序也理一遍？\n"
                                       "（音响不看文件名，按歌拷进 U 盘的先后放；理一遍是一瞬间的事，不改任何文件名。）"):
                return
            done = []
        else:
            if not messagebox.askyesno("确认编号",
                                       f"要把 {len(plan)} 首歌的文件名改成 001-、002- 这样吗？\n"
                                       f"改完顺手把 U 盘里的播放顺序也理好。\n\n改错了可以点「撤销上次编号」退回。"):
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
        try:
            in_order = reorder_directory(state["folder"], [t[0] for t in state["items"]])
        except OSError as e:
            load(state["folder"])
            messagebox.showwarning("编号好了，顺序没理完",
                                   f"文件名都改好了，但 U 盘里的播放顺序没理成，可能有东西正占着。\n"
                                   f"把播放器和资源管理器关了，再点一次「一键编号」就行。\n\n{e}")
            return
        load(state["folder"])
        head = f"已经给 {len(done)} 首歌编好号，" if done else "文件名没动，"
        if in_order:
            messagebox.showinfo("好了", head + "U 盘里的播放顺序也理好了。\n\n插到音响上就是按这个顺序放。")
        else:
            messagebox.showwarning("顺序没完全理好",
                                   head + "但 U 盘目录里的顺序还没完全对上。\n"
                                   "再点一次「一键编号」通常就好；还不行就把歌先拷到电脑上，清空 U 盘再拷回去。")

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

    def refresh_where():
        d = playlist_dir(state["settings"])
        if os.path.normcase(d) == os.path.normcase(desktop_dir()):
            where_var.set("歌单存到：桌面")
        else:
            tail = os.path.basename(d.rstrip("\\/")) or d
            where_var.set(f"歌单存到：{tail}")

    def choose_playlist_dir():
        d = filedialog.askdirectory(initialdir=playlist_dir(state["settings"]),
                                    title="以后出的歌单都存到这个文件夹（选桌面就是存桌面）")
        if not d:
            return
        state["settings"]["playlist_dir"] = os.path.normpath(d)
        try:
            save_settings(state["settings"])
        except OSError as e:
            messagebox.showerror("没记住", f"这个设置存不下来：\n{e}")
        refresh_where()

    refresh_where()

    def do_playlist():
        if not state["items"]:
            messagebox.showinfo("先打开", "先点左上角「打开 U 盘 / 文件夹」。")
            return
        out_dir = playlist_dir(state["settings"])
        try:
            out = write_playlist(out_dir, state["items"])
        except OSError as e:
            messagebox.showerror("没存成", f"歌单存不进这个文件夹：\n{out_dir}\n\n{e}")
            return
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
