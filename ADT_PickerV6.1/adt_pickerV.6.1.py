"""
ADT Map Tile Picker — v6.1 avec système de layers
"""

import json, os, re, shutil, struct, tkinter as tk
from tkinter import filedialog, messagebox, colorchooser
from pathlib import Path

# ─── Constantes ───────────────────────────────────────────────────────────────

GRID_SIZE  = 64
CELL_SIZE  = 10
CELL_GAP   = 1
MIN_CELL   = 6
MAX_CELL   = 20

COLOR_BG       = "#1a1a1a"
COLOR_SURFACE  = "#222222"
COLOR_PANEL    = "#1e1e1e"
COLOR_EMPTY    = "#2a2a2a"
COLOR_EMPTY_BD = "#333333"
COLOR_SELECTED = "#BA7517"
COLOR_HOVER_S  = "#d4881e"
COLOR_TEXT     = "#cccccc"
COLOR_TEXT_DIM = "#666666"
COLOR_ACCENT   = "#BA7517"
COLOR_SEP      = "#333333"
COLOR_GHOST    = "#444444"   # couleur fantôme pendant le drag spatial

# Palette de couleurs auto-assignées aux layers
LAYER_PALETTE = [
    "#1D9E75", "#4A90D9", "#9B59B6", "#E74C3C",
    "#F39C12", "#1ABC9C", "#E91E8C", "#27AE60",
    "#2980B9", "#D35400", "#8E44AD", "#16A085",
]

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "adt_picker_config.json"

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}

def save_config(data):
    try:
        cfg = load_config()
        cfg.update(data)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass

# ─── Layer ────────────────────────────────────────────────────────────────────

class Layer:
    _counter = 0

    def __init__(self, name: str, color: str, file_map: dict):
        Layer._counter += 1
        self.id       = Layer._counter
        self.name     = name
        self.color    = color
        self.file_map = file_map   # key "x_y" → chemin absolu

    def hover_color(self):
        """Version légèrement plus claire pour le hover."""
        r = int(self.color[1:3], 16)
        g = int(self.color[3:5], 16)
        b = int(self.color[5:7], 16)
        r = min(255, r + 40)
        g = min(255, g + 40)
        b = min(255, b + 40)
        return f"#{r:02x}{g:02x}{b:02x}"



# ─── Flags MPHD (partagés export + éditeur WDT) ───────────────────────────────

MPHD_FLAGS = [
    (0x0001, "0x1    :  wdt_uses_global_map_obj"),
    (0x0002, "0x2    :  adt_has_mccv  (vertex colors)"),
    (0x0004, "0x4    :  adt_has_big_alpha  (MCAL 4096 bytes)"),
    (0x0008, "0x8    :  adt_has_doodadrefs_sorted"),
    (0x0010, "0x10   :  adt_has_lightingvertices"),
    (0x0020, "0x20   :  adt_has_upside_down_ground"),
    (0x0040, "0x40   :  inconnu"),
    (0x0080, "0x80   :  adt_has_height_texturing"),
    (0x0100, "0x100  :  adt_has_maid  (charge _lod.adt)"),
    (0x8000, "0x8000 :  implicite continents  (Azeroth, Kalimdor…)"),
]


# ─── Flags MPHD ───────────────────────────────────────────────────────────────

MPHD_FLAGS = [
    (0x0001, "0x1    :  wdt_uses_global_map_obj"),
    (0x0002, "0x2    :  adt_has_mccv  (vertex colors)"),
    (0x0004, "0x4    :  adt_has_big_alpha  (MCAL 4096 bytes)"),
    (0x0008, "0x8    :  adt_has_doodadrefs_sorted"),
    (0x0010, "0x10   :  adt_has_lightingvertices"),
    (0x0020, "0x20   :  adt_has_upside_down_ground"),
    (0x0040, "0x40   :  inconnu"),
    (0x0080, "0x80   :  adt_has_height_texturing"),
    (0x0100, "0x100  :  adt_has_maid  (charge _lod.adt)"),
    (0x8000, "0x8000 :  implicite continents  (Azeroth, Kalimdor…)"),
]

def parse_wdt(data: bytes):
    pos = 0; mphd_flags = 0; tile_keys = set()
    while pos + 8 <= len(data):
        magic = data[pos:pos+4][::-1].decode("latin-1")
        size  = struct.unpack_from("<I", data, pos+4)[0]
        body  = data[pos+8:pos+8+size]
        if magic == "MPHD" and len(body) >= 4:
            mphd_flags = struct.unpack_from("<I", body, 0)[0]
        elif magic == "MAIN":
            for y in range(64):
                for x in range(64):
                    off = (y*64+x)*8
                    if off+4 <= len(body) and struct.unpack_from("<I", body, off)[0] & 0x1:
                        tile_keys.add(f"{x}_{y}")
        pos += 8 + size
    return mphd_flags, tile_keys

# ─── Génération WDT ───────────────────────────────────────────────────────────

def make_chunk(magic, data):
    return magic[::-1].encode("latin-1") + struct.pack("<I", len(data)) + bytes(data)

def generate_wdt(tile_keys: set, mphd_flags: int = 0) -> bytes:
    """
    Génère un fichier WDT minimal (MVER + MPHD + MAIN).
    tile_keys : ensemble de strings "x_y" des tiles présentes.
    mphd_flags : flags MPHD (0 = vanilla safe).
    """
    import struct as _struct
    # MVER
    mver = make_chunk("MVER", _struct.pack("<I", 18))
    # MPHD (32 bytes : flags + 7 uint32 padding)
    mphd_data = _struct.pack("<I", mphd_flags) + b'\x00' * 28
    mphd = make_chunk("MPHD", mphd_data)
    # MAIN : 64*64 entrées de 8 bytes chacune
    main_data = bytearray(64 * 64 * 8)
    for key in tile_keys:
        x, y = map(int, key.split("_"))
        if 0 <= x < 64 and 0 <= y < 64:
            offset = (y * 64 + x) * 8
            _struct.pack_into("<I", main_data, offset, 0x1)   # flag tile exists
    main = make_chunk("MAIN", main_data)
    return mver + mphd + main


def parse_wdt(data: bytes):
    """
    Lit un WDT et retourne (mphd_flags: int, tile_keys: set).
    tile_keys = ensemble de "x_y" dont le flag 0x1 est set dans MAIN.
    """
    pos = 0
    mphd_flags = 0
    tile_keys = set()
    while pos + 8 <= len(data):
        magic = data[pos:pos+4][::-1].decode("latin-1")
        size  = struct.unpack_from("<I", data, pos+4)[0]
        body  = data[pos+8 : pos+8+size]
        if magic == "MPHD" and len(body) >= 4:
            mphd_flags = struct.unpack_from("<I", body, 0)[0]
        elif magic == "MAIN":
            for y in range(64):
                for x in range(64):
                    off = (y*64 + x)*8
                    if off+4 <= len(body):
                        f = struct.unpack_from("<I", body, off)[0]
                        if f & 0x1:
                            tile_keys.add(f"{x}_{y}")
        pos += 8 + size
    return mphd_flags, tile_keys

# ─── App ──────────────────────────────────────────────────────────────────────

class ADTPickerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ADT Map Tile Picker")
        self.configure(bg=COLOR_BG)
        self.resizable(True, True)
        self.minsize(900, 600)

        self.layers        = []      # list[Layer], index 0 = priorité haute
        self.selected      = set()   # keys sélectionnées
        self.cell_size     = CELL_SIZE
        self.hover_key       = None
        self.drag_mode       = None
        self._grid_cell_size = -1
        self.dragging      = False
        self.active_layer  = None
        self.move_mode       = False
        self.drag_layer_start = None   # (x,y) case départ du drag spatial
        self.drag_preview_keys = {}    # key_new → key_old pendant l'aperçu
        self.brush_radius     = 1
        self.hover_brush_keys = set()
        self.export_folder = load_config().get("export_folder", "")
        self.export_name   = load_config().get("export_name", "")

        self._build_ui()
        self._show_drop_state()
        # Restaurer géométrie
        geo = load_config().get("window_geometry", "")
        if geo:
            try: self.geometry(geo)
            except Exception: pass
        # Sauvegarder à chaque changement de taille/position
        self.bind("<Configure>", self._on_window_configure)
        self._save_geo_job = None

    def _on_window_configure(self, event):
        """Sauvegarde la géométrie avec debounce (évite les écritures en rafale)."""
        if event.widget is not self: return
        if self._save_geo_job:
            self.after_cancel(self._save_geo_job)
        self._save_geo_job = self.after(400, self._save_geometry)

    def _save_geometry(self):
        save_config({"window_geometry": self.geometry()})
        self._save_geo_job = None

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Barre titre
        top = tk.Frame(self, bg=COLOR_BG, padx=12, pady=8)
        top.pack(fill=tk.X)
        self.lbl_title = tk.Label(top, text="ADT Map Tile Picker",
                                   bg=COLOR_BG, fg=COLOR_TEXT,
                                   font=("Consolas", 13, "bold"))
        self.lbl_title.pack(side=tk.LEFT)
        self.lbl_stats = tk.Label(top, text="", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                                   font=("Consolas", 10))
        self.lbl_stats.pack(side=tk.RIGHT)
        tk.Button(top, text="?", command=self._open_help,
                  bg="#2a2a2a", fg="#888888", relief=tk.FLAT,
                  font=("Consolas", 10, "bold"), padx=8, pady=3,
                  cursor="hand2", bd=0, activebackground="#333333",
                  activeforeground="#aaaaaa"
                  ).pack(side=tk.RIGHT, padx=(0, 4))
        tk.Button(top, text="Edit WDT", command=self._open_wdt_editor,
                  bg="#2a2a2a", fg="#4A90D9", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=10, pady=3,
                  cursor="hand2", bd=0, activebackground="#333333",
                  activeforeground="#6aaeed"
                  ).pack(side=tk.RIGHT, padx=(0, 6))

        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Corps principal : grille + panel layers
        body = tk.Frame(self, bg=COLOR_BG)
        body.pack(fill=tk.BOTH, expand=True)

        # ── Grille ───────────────────────────────────────────────────────────
        grid_frame = tk.Frame(body, bg=COLOR_BG)
        grid_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.canvas = tk.Canvas(grid_frame, bg=COLOR_SURFACE,
                                highlightthickness=0, cursor="crosshair")
        vbar = tk.Scrollbar(grid_frame, orient=tk.VERTICAL,   command=self.canvas.yview)
        hbar = tk.Scrollbar(grid_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        vbar.pack(side=tk.RIGHT,  fill=tk.Y)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>",          self._on_hover)
        self.canvas.bind("<Leave>",           self._on_leave)
        self.canvas.bind("<MouseWheel>",      self._on_scroll)
        self.canvas.bind("<Button-4>",        self._on_scroll)
        self.canvas.bind("<Button-5>",        self._on_scroll)

        # ── Panel layers (droite) ────────────────────────────────────────────
        tk.Frame(body, bg=COLOR_SEP, width=1).pack(side=tk.LEFT, fill=tk.Y)

        panel = tk.Frame(body, bg=COLOR_PANEL, width=280)
        panel.pack(side=tk.LEFT, fill=tk.Y)
        panel.pack_propagate(False)  # width=280

        ph = tk.Frame(panel, bg=COLOR_PANEL, padx=10, pady=8)
        ph.pack(fill=tk.X)
        tk.Label(ph, text="LAYERS", bg=COLOR_PANEL, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 9, "bold")).pack(side=tk.LEFT)

        # Boutons ajout layer
        pb = tk.Frame(ph, bg=COLOR_PANEL)
        pb.pack(side=tk.RIGHT)
        for txt, cmd in [("dossier", self._add_layer_folder),
                          ("fichiers", self._add_layer_files)]:
            tk.Button(pb, text=f"+ {txt}", command=cmd,
                      bg="#2a2a2a", fg=COLOR_TEXT_DIM,
                      relief=tk.FLAT, font=("Consolas", 8),
                      padx=6, pady=2, cursor="hand2", bd=0,
                      activebackground="#333333").pack(side=tk.LEFT, padx=2)

        tk.Frame(panel, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Liste des layers (scrollable)
        lf = tk.Frame(panel, bg=COLOR_PANEL)
        lf.pack(fill=tk.BOTH, expand=True)
        lsb = tk.Scrollbar(lf, orient=tk.VERTICAL)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.layer_canvas = tk.Canvas(lf, bg=COLOR_PANEL,
                                       highlightthickness=0,
                                       yscrollcommand=lsb.set)
        lsb.config(command=self.layer_canvas.yview)
        self.layer_canvas.pack(fill=tk.BOTH, expand=True)
        self.layer_list_frame = tk.Frame(self.layer_canvas, bg=COLOR_PANEL)
        self.layer_canvas.create_window((0, 0), window=self.layer_list_frame,
                                         anchor="nw")
        self.layer_list_frame.bind("<Configure>", lambda e: self.layer_canvas.configure(
            scrollregion=self.layer_canvas.bbox("all")))

        # Bas du panel : vider tout
        tk.Frame(panel, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        tk.Button(panel, text="Vider tous les layers",
                  command=self._clear_all_layers,
                  bg=COLOR_PANEL, fg="#cc4444",
                  relief=tk.FLAT, font=("Consolas", 9),
                  padx=10, pady=6, cursor="hand2", bd=0,
                  activebackground="#2a2a2a").pack(fill=tk.X)

        # ── Tooltip ──────────────────────────────────────────────────────────
        self.tooltip = tk.Toplevel(self)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.withdraw()
        self.tooltip.configure(bg="#111111")
        self.lbl_tooltip = tk.Label(self.tooltip, bg="#111111", fg="#aaaaaa",
                                     font=("Consolas", 9), padx=6, pady=3)
        self.lbl_tooltip.pack()

        # ── Barre outils bas ─────────────────────────────────────────────────
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        bot = tk.Frame(self, bg=COLOR_BG, padx=8, pady=6)
        bot.pack(fill=tk.X)

        btn = dict(bg=COLOR_SURFACE, fg=COLOR_TEXT, relief=tk.FLAT,
                   font=("Consolas", 10), padx=8, pady=4,
                   activebackground="#333333", activeforeground=COLOR_TEXT,
                   cursor="hand2", bd=0)

        self.btn_all    = tk.Button(bot, text="Tout sélectionner",
                                     command=self._select_all, **btn)
        self.btn_all.pack(side=tk.LEFT, padx=2)
        self.btn_clear  = tk.Button(bot, text="Vider sél.",
                                     command=self._clear_sel, **btn)
        self.btn_clear.pack(side=tk.LEFT, padx=2)
        self.btn_invert = tk.Button(bot, text="Inverser",
                                     command=self._invert_sel, **btn)
        self.btn_invert.pack(side=tk.LEFT, padx=2)

        tk.Frame(bot, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.btn_zoom_in  = tk.Button(bot, text="+", command=self._zoom_in,  **btn, width=2)
        self.btn_zoom_in.pack(side=tk.LEFT, padx=2)
        self.btn_zoom_out = tk.Button(bot, text="−", command=self._zoom_out, **btn, width=2)
        self.btn_zoom_out.pack(side=tk.LEFT, padx=2)

        tk.Frame(bot, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        self.btn_move_mode = tk.Button(bot, text="↔ Déplacer ADTs",
                                        command=self._toggle_move_mode,
                                        bg=COLOR_SURFACE, fg=COLOR_TEXT_DIM,
                                        relief=tk.FLAT, font=("Consolas", 9),
                                        padx=8, pady=4, cursor="hand2", bd=0,
                                        activebackground="#333333")
        self.btn_move_mode.pack(side=tk.LEFT, padx=2)
        self.lbl_move_hint = tk.Label(bot, text="aucun layer actif", bg=COLOR_BG,
                                       fg="#4A90D9", font=("Consolas", 8))
        self.lbl_move_hint.pack(side=tk.LEFT, padx=4)

        self.lbl_coord = tk.Label(bot, text="", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                                   font=("Consolas", 9))
        self.lbl_coord.pack(side=tk.RIGHT, padx=8)
        tk.Frame(bot, bg="#444", width=1).pack(side=tk.RIGHT, fill=tk.Y, padx=8)
        tk.Label(bot, text="Rayon :", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 9)).pack(side=tk.RIGHT)
        self.var_brush = tk.IntVar(value=1)
        self.spin_brush = tk.Spinbox(bot, from_=1, to=32, width=3,
                                      textvariable=self.var_brush,
                                      bg=COLOR_SURFACE, fg=COLOR_TEXT,
                                      buttonbackground=COLOR_SURFACE,
                                      relief=tk.FLAT, font=("Consolas", 9),
                                      command=self._on_brush_change)
        self.spin_brush.pack(side=tk.RIGHT, padx=(0,4))
        self.spin_brush.bind("<Return>",   lambda e: self._on_brush_change())
        self.spin_brush.bind("<FocusOut>", lambda e: self._on_brush_change())
        self.slider_brush = tk.Scale(bot, from_=1, to=32, orient=tk.HORIZONTAL,
                                      variable=self.var_brush, showvalue=False,
                                      bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                                      highlightthickness=0, troughcolor=COLOR_SURFACE,
                                      activebackground="#4A90D9", length=120,
                                      command=lambda v: self._on_brush_change())
        self.slider_brush.pack(side=tk.RIGHT, padx=(0,4))

        # ── Barre export ──────────────────────────────────────────────────────
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        exp = tk.Frame(self, bg=COLOR_BG, padx=8, pady=6)
        exp.pack(fill=tk.X)

        self.btn_export_fast = tk.Button(exp, text="⚡ Exporter",
                                          command=self._export_fast,
                                          bg="#1a5c38", fg="#fff",
                                          relief=tk.FLAT, font=("Consolas", 10, "bold"),
                                          padx=12, pady=4, cursor="hand2", bd=0,
                                          activebackground="#144d2f",
                                          state=tk.DISABLED)
        self.btn_export_fast.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_export = tk.Button(exp, text="Exporter vers…",
                                     command=self._export,
                                     bg=COLOR_ACCENT, fg="#fff",
                                     relief=tk.FLAT, font=("Consolas", 10, "bold"),
                                     padx=12, pady=4, cursor="hand2", bd=0,
                                     activebackground="#9a6010",
                                     state=tk.DISABLED)
        self.btn_export.pack(side=tk.LEFT)

        tk.Label(exp, text="Dossier mémorisé :", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(16, 6))
        self.var_folder = tk.StringVar()
        self.lbl_folder = tk.Label(exp, textvariable=self.var_folder,
                                    bg=COLOR_BG, fg=COLOR_TEXT,
                                    font=("Consolas", 9), cursor="hand2", width=40)
        self.lbl_folder.pack(side=tk.LEFT)
        self.lbl_folder.bind("<Button-1>", lambda e: self._pick_folder())
        tk.Button(exp, text="✕", command=self._clear_folder,
                  bg=COLOR_BG, fg=COLOR_TEXT_DIM, relief=tk.FLAT,
                  font=("Consolas", 9), padx=4, pady=0, cursor="hand2", bd=0,
                  activebackground=COLOR_BG, activeforeground="#cc4444"
                  ).pack(side=tk.LEFT, padx=2)
        # Nom de map mémorisé
        tk.Label(exp, text="Nom mémorisé :", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(16, 6))
        self.var_name = tk.StringVar()
        self.lbl_name_mem = tk.Label(exp, textvariable=self.var_name,
                                      bg=COLOR_BG, fg=COLOR_TEXT,
                                      font=("Consolas", 9), cursor="hand2", width=16)
        self.lbl_name_mem.pack(side=tk.LEFT)
        self.lbl_name_mem.bind("<Button-1>", lambda e: self._pick_export_name())
        tk.Button(exp, text="✕", command=self._clear_export_name,
                  bg=COLOR_BG, fg=COLOR_TEXT_DIM, relief=tk.FLAT,
                  font=("Consolas", 9), padx=4, pady=0, cursor="hand2", bd=0,
                  activebackground=COLOR_BG, activeforeground="#cc4444"
                  ).pack(side=tk.LEFT, padx=2)
        self._refresh_folder_ui()

    # ─── Drop state ──────────────────────────────────────────────────────────

    def _show_drop_state(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 640
        h = self.canvas.winfo_height() or 400
        self.canvas.create_text(w//2, h//2 - 14,
                                 text="Ajoute un layer via les boutons  + dossier / + fichiers",
                                 fill=COLOR_TEXT_DIM, font=("Consolas", 11))
        self.canvas.create_text(w//2, h//2 + 10,
                                 text="Chaque chargement crée un layer indépendant",
                                 fill=COLOR_TEXT_DIM, font=("Consolas", 10))

    # ─── Layer management ────────────────────────────────────────────────────

    def _next_color(self):
        return LAYER_PALETTE[len(self.layers) % len(LAYER_PALETTE)]

    def _parse_paths(self, paths):
        pattern = re.compile(r'^(.+)_(\d+)_(\d+)\.adt$', re.IGNORECASE)
        file_map = {}
        map_name = ""
        for p in paths:
            m = pattern.match(os.path.basename(p))
            if not m: continue
            x, y = int(m.group(2)), int(m.group(3))
            if not (0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE): continue
            if not map_name: map_name = m.group(1)
            file_map[f"{x}_{y}"] = p
        return file_map, map_name

    def _add_layer_folder(self):
        folder = filedialog.askdirectory(title="Sélectionner le dossier d'ADTs")
        if not folder: return
        paths = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.lower().endswith(".adt")]
        file_map, map_name = self._parse_paths(paths)
        if not file_map:
            messagebox.showwarning("Aucun ADT", "Aucun fichier .adt valide trouvé.")
            return
        name = os.path.basename(folder) or map_name or f"Layer {len(self.layers)+1}"
        self._create_layer(name, file_map)

    def _add_layer_files(self):
        files = filedialog.askopenfilenames(
            title="Sélectionner des fichiers ADT",
            filetypes=[("ADT files", "*.adt"), ("All files", "*.*")])
        if not files: return
        file_map, map_name = self._parse_paths(list(files))
        if not file_map:
            messagebox.showwarning("Aucun ADT", "Aucun fichier .adt valide trouvé.")
            return
        name = map_name or f"Layer {len(self.layers)+1}"
        self._create_layer(name, file_map)

    def _create_layer(self, name, file_map):
        layer = Layer(name, self._next_color(), file_map)
        self.layers.insert(0, layer)   # inséré en tête = priorité maximale
        self._rebuild_layer_panel()
        self._draw_grid()

    def _delete_layer(self, layer):
        if not messagebox.askyesno("Supprimer le layer",
                f"Supprimer \"{layer.name}\" et ses {len(layer.file_map)} tuile(s) ?"):
            return
        # Désélectionner les tuiles qui n'existent plus dans d'autres layers
        remaining_keys = set()
        for l in self.layers:
            if l is not layer:
                remaining_keys |= l.file_map.keys()
        self.selected &= remaining_keys
        self.layers.remove(layer)
        self._rebuild_layer_panel()
        if self.layers:
            self._draw_grid()
        else:
            self.canvas.delete("all")
            self._show_drop_state()
        self._update_stats()

    def _rename_layer(self, layer, lbl_name):
        dialog = tk.Toplevel(self)
        dialog.title("Renommer")
        dialog.configure(bg=COLOR_BG)
        dialog.resizable(False, False)
        dialog.geometry(f"+{self.winfo_rootx()+200}+{self.winfo_rooty()+200}")
        dialog.grab_set()
        dialog.lift()

        tk.Label(dialog, text="Nouveau nom :", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 10)).pack(anchor="w", padx=14, pady=(14, 4))

        var = tk.StringVar(value=layer.name)
        entry = tk.Entry(dialog, textvariable=var, bg=COLOR_SURFACE, fg=COLOR_TEXT,
                         insertbackground=COLOR_TEXT, relief=tk.FLAT,
                         font=("Consolas", 10), width=26)
        entry.pack(padx=14, pady=(0, 12))
        dialog.after(50, lambda: (entry.focus_force(), entry.select_range(0, tk.END)))

        def confirm(e=None):
            name = var.get().strip()
            if name:
                layer.name = name
                lbl_name.config(text=name[:16])
            dialog.destroy()

        entry.bind("<Return>", confirm)
        entry.bind("<Escape>", lambda e: dialog.destroy())
        tk.Button(dialog, text="OK", command=confirm,
                  bg=COLOR_ACCENT, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 10, "bold"), padx=20, pady=5,
                  cursor="hand2", bd=0).pack(pady=(0, 14))

    def _recolor_layer(self, layer, swatch):
        color = colorchooser.askcolor(color=layer.color, title="Couleur du layer")[1]
        if not color: return
        layer.color = color
        swatch.config(bg=color)
        self._draw_grid()

    def _move_layer(self, layer, direction):
        idx = self.layers.index(layer)
        new_idx = idx + direction
        if not (0 <= new_idx < len(self.layers)): return
        # Swap propre
        self.layers[idx], self.layers[new_idx] = self.layers[new_idx], self.layers[idx]
        self._rebuild_layer_panel()
        self._draw_grid()

    def _rebuild_layer_panel(self):
        for w in self.layer_list_frame.winfo_children():
            w.destroy()

        for i, layer in enumerate(self.layers):
            self._build_layer_row(layer, i)

        self._update_stats()

    def _build_layer_row(self, layer, idx):
        is_active = (layer is self.active_layer)
        row_bg = "#1a2a3a" if is_active else COLOR_PANEL
        row = tk.Frame(self.layer_list_frame, bg=row_bg, pady=4, padx=8)
        row.pack(fill=tk.X)
        tk.Frame(self.layer_list_frame, bg=COLOR_SEP, height=1).pack(fill=tk.X)


        # Priorité badge
        tk.Button(row, text="▶",
                   command=lambda l=layer: self._set_active_layer(l),
                   bg="#1a3a5c" if is_active else row_bg,
                   fg="#4A90D9" if is_active else COLOR_TEXT_DIM,
                   relief=tk.FLAT, font=("Consolas", 8), padx=3, pady=0,
                   cursor="hand2", bd=0,
                   activebackground="#1a3a5c", activeforeground="#4A90D9"
                   ).pack(side=tk.LEFT)
        tk.Label(row, text=f"#{idx+1}", bg=row_bg,
                 fg="#4A90D9" if is_active else COLOR_TEXT_DIM,
                 font=("Consolas", 8), width=3, anchor="e").pack(side=tk.LEFT)
        swatch = tk.Label(row, bg=layer.color, width=2, cursor="hand2", relief=tk.FLAT)
        swatch.pack(side=tk.LEFT, padx=(4, 6))
        lbl_name = tk.Label(row, text=layer.name[:16], bg=row_bg,
                             fg="#6aaeed" if is_active else COLOR_TEXT,
                             font=("Consolas", 9), cursor="hand2", anchor="w", width=12)
        lbl_name.pack(side=tk.LEFT)

        # Swatch + nom → interactions
        swatch.bind("<Button-1>",   lambda e, l=layer, s=swatch: self._recolor_layer(l, s))
        lbl_name.bind("<Button-1>", lambda e, l=layer, n=lbl_name: self._rename_layer(l, n))

        # Compteur tuiles
        n = len(layer.file_map)
        tk.Label(row, text=f"{n}t", bg=COLOR_PANEL, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 8), width=4, anchor="e").pack(side=tk.LEFT)

        # Boutons ordre + supprimer
        btn_mini = dict(bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, relief=tk.FLAT,
                        font=("Consolas", 9), padx=3, pady=0,
                        cursor="hand2", bd=0, activebackground="#2a2a2a")
        tk.Button(row, text="▲", command=lambda l=layer: self._move_layer(l, -1),
                  **btn_mini).pack(side=tk.RIGHT)
        tk.Button(row, text="▼", command=lambda l=layer: self._move_layer(l, +1),
                  **btn_mini).pack(side=tk.RIGHT)
        tk.Button(row, text="✕", command=lambda l=layer: self._delete_layer(l),
                  activeforeground="#cc4444", **btn_mini).pack(side=tk.RIGHT, padx=(0,2))


    def _set_active_layer(self, layer):
        # Toggle : clic sur le layer déjà actif → désélection
        if self.active_layer is layer:
            self.active_layer = None
        else:
            self.active_layer = layer
        self._rebuild_layer_panel()
        self._update_move_hint()

    def _update_move_hint(self):
        if not hasattr(self, "lbl_move_hint"): return
        if self.move_mode:
            if self.active_layer:
                self.lbl_move_hint.config(
                    text=f"▶ {self.active_layer.name[:18]}  │  drag=déplacer  Shift+drag=transférer  Ctrl+drag=supprimer")
            else:
                self.lbl_move_hint.config(text="Sélectionne un layer avec ▶ pour commencer")
        else:
            if self.active_layer:
                self.lbl_move_hint.config(text=f"▶ {self.active_layer.name[:24]}  │  Ctrl+drag=supprimer")
            else:
                self.lbl_move_hint.config(text="aucun layer actif")

    def _toggle_move_mode(self):
        self.move_mode = not self.move_mode
        if self.move_mode:
            self.btn_move_mode.config(bg="#1a3a5c", fg="#4A90D9", font=("Consolas", 9, "bold"))
            self.canvas.config(cursor="fleur")
        else:
            self.btn_move_mode.config(bg=COLOR_SURFACE, fg=COLOR_TEXT_DIM, font=("Consolas", 9))
            self.canvas.config(cursor="crosshair")
            # Annuler tout aperçu en cours
            if self.drag_preview_keys:
                self._cancel_layer_drag_preview()
        self._update_move_hint()

    def _move_tile_to_active(self, x, y):
        if not self.active_layer: return
        key = f"{x}_{y}"
        src = next((l for l in self.layers if key in l.file_map), None)
        if src is None or src is self.active_layer: return
        self.active_layer.file_map[key] = src.file_map.pop(key)
        c = self.active_layer.color
        self.canvas.itemconfig(f"cell_{key}", fill=c, outline=c)
        self._rebuild_layer_panel()
        self._update_stats()


    def _start_layer_drag(self, x, y):
        """Démarre un drag spatial sur le layer actif."""
        if not self.active_layer: return False
        if not self.active_layer.file_map: return False
        self.drag_layer_start = (x, y)
        self.drag_preview_keys = {}
        return True

    def _update_layer_drag_preview(self, x, y):
        """Met à jour l'aperçu en temps réel du déplacement spatial."""
        if not self.drag_layer_start or not self.active_layer: return
        sx, sy = self.drag_layer_start
        dx, dy = x - sx, y - sy
        if dx == 0 and dy == 0 and not self.drag_preview_keys:
            return  # pas encore bougé

        # Effacer l'aperçu précédent
        all_keys = self._all_present_keys()
        for new_key, old_key in self.drag_preview_keys.items():
            nx, ny = map(int, new_key.split("_"))
            # Restaurer la case preview (maintenant vide ou autre layer)
            real_color = self._key_color(new_key)
            if new_key in self.selected:
                self.canvas.itemconfig(f"cell_{new_key}", fill=COLOR_SELECTED, outline=COLOR_SELECTED)
            elif real_color:
                self.canvas.itemconfig(f"cell_{new_key}", fill=real_color, outline=real_color)
            else:
                self.canvas.itemconfig(f"cell_{new_key}", fill=COLOR_EMPTY, outline=COLOR_EMPTY_BD)
        # Restaurer les cases fantômes (les originales du layer)
        for old_key in self.active_layer.file_map:
            ox, oy = map(int, old_key.split("_"))
            nx, ny = ox+dx, oy+dy
            new_key = f"{nx}_{ny}"
            if new_key not in self.active_layer.file_map:
                # La case originale devient fantôme
                self.canvas.itemconfig(f"cell_{old_key}", fill=COLOR_GHOST, outline=COLOR_GHOST)

        # Dessiner le nouvel aperçu
        new_preview = {}
        for old_key in self.active_layer.file_map:
            ox, oy = map(int, old_key.split("_"))
            nx, ny = ox+dx, oy+dy
            if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE:
                new_key = f"{nx}_{ny}"
                new_preview[new_key] = old_key
                self.canvas.itemconfig(f"cell_{new_key}",
                                        fill=self.active_layer.color,
                                        outline=self.active_layer.color)
        self.drag_preview_keys = new_preview

    def _apply_layer_drag(self, x, y):
        """Applique définitivement le déplacement spatial du layer actif."""
        if not self.drag_layer_start or not self.active_layer: return
        sx, sy = self.drag_layer_start
        dx, dy = x - sx, y - sy
        self.drag_layer_start = None
        self.drag_preview_keys = {}
        if dx == 0 and dy == 0: return
        # Déplacer toutes les clés
        new_file_map = {}
        for key, path in self.active_layer.file_map.items():
            ox, oy = map(int, key.split("_"))
            nx, ny = ox+dx, oy+dy
            if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE:
                new_file_map[f"{nx}_{ny}"] = path
        self.active_layer.file_map = new_file_map
        self.selected.clear()   # la sélection ne suit pas le déplacement
        self._draw_grid()
        self._rebuild_layer_panel()

    def _cancel_layer_drag_preview(self):
        """Annule l'aperçu sans appliquer le déplacement."""
        self.drag_preview_keys = {}
        self.drag_layer_start = None
        self._draw_grid()

    def _delete_tile_from_active(self, x, y):
        """Supprime les tiles dans le brush du layer actif (Ctrl+clic)."""
        if not self.active_layer: return
        changed = False
        for key in self._brush_keys(x, y):
            if key not in self.active_layer.file_map: continue
            del self.active_layer.file_map[key]
            self.selected.discard(key)
            c = self._key_color(key)
            if c: self.canvas.itemconfig(f"cell_{key}", fill=c, outline=c)
            else: self.canvas.itemconfig(f"cell_{key}", fill=COLOR_EMPTY, outline=COLOR_EMPTY_BD)
            changed = True
        if changed:
            self._rebuild_layer_panel()
            self._update_stats()


    def _on_brush_change(self):
        try:
            r = int(self.var_brush.get())
            self.brush_radius = max(1, min(32, r))
            self.var_brush.set(self.brush_radius)
        except (ValueError, tk.TclError):
            self.var_brush.set(self.brush_radius)

    def _brush_keys(self, cx, cy):
        r = self.brush_radius - 1
        keys = set()
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                nx, ny = cx+dx, cy+dy
                if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE:
                    keys.add(f"{nx}_{ny}")
        return keys

    # ─── Grille ──────────────────────────────────────────────────────────────

    def _merged_file_map(self):
        """Fusionne les layers par priorité (index 0 = haute priorité)."""
        merged = {}
        for layer in reversed(self.layers):   # du moins prioritaire au plus
            merged.update(layer.file_map)
        return merged

    def _key_color(self, key):
        """Retourne la couleur de la tuile selon le layer prioritaire."""
        for layer in self.layers:
            if key in layer.file_map:
                return layer.color
        return None

    def _cell_fill(self, key, all_keys, multi):
        if key in self.selected: return COLOR_SELECTED, COLOR_SELECTED
        if key in all_keys:
            c = self._key_color(key)
            return c, ("#ffffff" if key in multi else c)
        return COLOR_EMPTY, COLOR_EMPTY_BD

    def _draw_grid(self, force=False):
        cs   = self.cell_size
        step = cs + CELL_GAP
        all_keys = set()
        for l in self.layers: all_keys |= l.file_map.keys()
        multi = {k for k in all_keys
                 if sum(1 for l in self.layers if k in l.file_map) > 1}

        need_build = (force or self._grid_cell_size != cs
                      or len(self.canvas.find_withtag("cell")) != GRID_SIZE*GRID_SIZE)

        if need_build:
            self.canvas.delete("all")
            self.canvas.configure(scrollregion=(0, 0, step*GRID_SIZE, step*GRID_SIZE))
            for y in range(GRID_SIZE):
                for x in range(GRID_SIZE):
                    key = f"{x}_{y}"
                    x0, y0 = x*step, y*step
                    fill, bd = self._cell_fill(key, all_keys, multi)
                    self.canvas.create_rectangle(x0, y0, x0+cs, y0+cs,
                                                  fill=fill, outline=bd,
                                                  tags=(f"cell_{key}", "cell"))
            self._grid_cell_size = cs
        else:
            for y in range(GRID_SIZE):
                for x in range(GRID_SIZE):
                    key = f"{x}_{y}"
                    if key in self.hover_brush_keys: continue
                    fill, bd = self._cell_fill(key, all_keys, multi)
                    self.canvas.itemconfig(f"cell_{key}", fill=fill, outline=bd)
        self._update_stats()

    # ─── Sélection ───────────────────────────────────────────────────────────

    def _all_present_keys(self):
        keys = set()
        for l in self.layers: keys |= l.file_map.keys()
        return keys

    def _toggle_cell(self, x, y):
        if x is None: return
        all_keys = self._all_present_keys()
        for key in self._brush_keys(x, y):
            if key not in all_keys: continue
            if self.drag_mode == "select":
                self.selected.add(key)
                self.canvas.itemconfig(f"cell_{key}", fill=COLOR_SELECTED, outline=COLOR_SELECTED)
            else:
                self.selected.discard(key)
                c = self._key_color(key) or COLOR_EMPTY
                self.canvas.itemconfig(f"cell_{key}", fill=c, outline=c)
        self._update_stats()

    def _select_all(self):
        for key in self._all_present_keys():
            self.selected.add(key)
            self.canvas.itemconfig(f"cell_{key}", fill=COLOR_SELECTED, outline=COLOR_SELECTED)
        self._update_stats()

    def _clear_sel(self):
        for key in list(self.selected):
            self.selected.discard(key)
            c = self._key_color(key) or COLOR_EMPTY
            self.canvas.itemconfig(f"cell_{key}", fill=c, outline=c)
        self._update_stats()

    def _invert_sel(self):
        all_keys = self._all_present_keys()
        for key in all_keys:
            if key in self.selected:
                self.selected.discard(key)
                c = self._key_color(key)
                self.canvas.itemconfig(f"cell_{key}", fill=c, outline=c)
            else:
                self.selected.add(key)
                self.canvas.itemconfig(f"cell_{key}", fill=COLOR_SELECTED, outline=COLOR_SELECTED)
        self._update_stats()

    def _clear_all_layers(self):
        if not self.layers: return
        if not messagebox.askyesno("Vider tous les layers",
                "Supprimer tous les layers et leurs données ?"):
            return
        self.layers.clear()
        self.selected.clear()
        self._rebuild_layer_panel()
        self.canvas.delete("all")
        self._show_drop_state()

    # ─── Interactions grille ─────────────────────────────────────────────────

    def _canvas_to_cell(self, cx, cy):
        step = self.cell_size + CELL_GAP
        x = int(self.canvas.canvasx(cx)) // step
        y = int(self.canvas.canvasy(cy)) // step
        if 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE:
            return x, y
        return None, None

    def _on_press(self, event):
        x, y = self._canvas_to_cell(event.x, event.y)
        if x is None: return
        key   = f"{x}_{y}"
        ctrl  = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)

        if self.move_mode:
            if ctrl:
                # Ctrl+drag  → supprimer tiles du layer actif
                self._delete_tile_from_active(x, y)
                self.dragging = True; self.drag_mode = "delete_active"
            elif shift:
                # Shift+drag → transférer tiles individuelles vers layer actif
                if key in self._all_present_keys():
                    self._move_tile_to_active(x, y)
                    self.dragging = True; self.drag_mode = "transfer"
            else:
                # Drag libre  → déplacement spatial du layer actif
                if self._start_layer_drag(x, y):
                    self.dragging = True; self.drag_mode = "layer_drag"
            return

        # Hors mode déplacement : Ctrl supprime, le reste sélectionne
        if ctrl and self.active_layer:
            self._delete_tile_from_active(x, y)
            self.dragging = True; self.drag_mode = "delete_active"
            return
        if key not in self._all_present_keys(): return
        self.dragging  = True
        self.drag_mode = "deselect" if key in self.selected else "select"
        self._toggle_cell(x, y)

    def _on_drag(self, event):
        if not self.dragging: return
        x, y = self._canvas_to_cell(event.x, event.y)
        if x is None: return
        if   self.drag_mode == "layer_drag":    self._update_layer_drag_preview(x, y)
        elif self.drag_mode == "delete_active": self._delete_tile_from_active(x, y)
        elif self.drag_mode == "transfer":      self._move_tile_to_active(x, y)
        else:                                   self._toggle_cell(x, y)

    def _on_release(self, event):
        if self.drag_mode == "layer_drag":
            x, y = self._canvas_to_cell(event.x, event.y)
            if x is not None: self._apply_layer_drag(x, y)
            else:              self._cancel_layer_drag_preview()
        self.dragging  = False
        self.drag_mode = None

    def _restore_brush_preview(self, all_keys):
        for k in self.hover_brush_keys:
            if k in self.selected:
                self.canvas.itemconfig(f"cell_{k}", fill=COLOR_SELECTED, outline=COLOR_SELECTED)
            elif k in all_keys:
                c = self._key_color(k)
                self.canvas.itemconfig(f"cell_{k}", fill=c, outline=c)
            else:
                self.canvas.itemconfig(f"cell_{k}", fill=COLOR_EMPTY, outline=COLOR_EMPTY_BD)
        self.hover_brush_keys.clear()

    def _on_hover(self, event):
        x, y = self._canvas_to_cell(event.x, event.y)
        key  = f"{x}_{y}" if x is not None else None
        all_keys = self._all_present_keys()

        if key != self.hover_key:
            self._restore_brush_preview(all_keys)
            if x is not None:
                brush = self._brush_keys(x, y)
                for k in brush:
                    if k in self.selected:
                        hc = COLOR_HOVER_S
                    elif k in all_keys:
                        hc = next((l.hover_color() for l in self.layers if k in l.file_map), COLOR_HOVER_S)
                    else:
                        hc = "#3a3a3a"
                    self.canvas.itemconfig(f"cell_{k}", fill=hc)
                self.hover_brush_keys = brush
            self.hover_key = key

        # Tooltip
        if key and x is not None:
            has = key in all_keys
            if has:
                # Trouver les layers qui ont cette tuile
                layer_names = [l.name for l in self.layers if key in l.file_map]
                winner = self.layers[next(i for i,l in enumerate(self.layers) if key in l.file_map)]
                sel_prefix = "✓ " if key in self.selected else ""
                info = f"{sel_prefix}{winner.name}  →  {os.path.basename(winner.file_map[key])}"
                if len(layer_names) > 1:
                    info += f"  [{len(layer_names)} layers]"
                self.lbl_tooltip.config(text=info)
            else:
                self.lbl_tooltip.config(text=f"[{x},{y}] — vide")
            rx = self.winfo_rootx() + event.x + 16
            ry = self.winfo_rooty() + event.y - 24
            self.tooltip.wm_geometry(f"+{rx}+{ry}")
            self.tooltip.deiconify()
            self.lbl_coord.config(text=f"x={x}  y={y}")
        else:
            self.tooltip.withdraw()
            self.lbl_coord.config(text="")

    def _on_leave(self, event):
        self.tooltip.withdraw()
        self.lbl_coord.config(text="")
        self._restore_brush_preview(self._all_present_keys())
        self.hover_key = None

    def _on_scroll(self, event):
        if event.num == 4 or event.delta > 0: self._zoom_in()
        elif event.num == 5 or event.delta < 0: self._zoom_out()

    # ─── Zoom ────────────────────────────────────────────────────────────────

    def _zoom_in(self):
        if self.cell_size < MAX_CELL:
            self.cell_size += 1
            if self.layers: self._draw_grid(force=True)

    def _zoom_out(self):
        if self.cell_size > MIN_CELL:
            self.cell_size -= 1
            if self.layers: self._draw_grid(force=True)

    # ─── Stats ───────────────────────────────────────────────────────────────

    def _update_stats(self):
        total = len(self._all_present_keys())
        s     = len(self.selected)
        nl    = len(self.layers)
        self.lbl_stats.config(
            text=f"{nl} layer{'s' if nl>1 else ''}  |  {total} tuiles  |  {s} sélectionnée{'s' if s>1 else ''}")
        has_sel    = s > 0
        has_folder = bool(self.export_folder)
        self.btn_export.config(state=tk.NORMAL if has_sel else tk.DISABLED)
        self.btn_export_fast.config(
            state=tk.NORMAL if (has_sel and has_folder) else tk.DISABLED,
            text=f"⚡ Exporter ({s})" if has_sel and has_folder else "⚡ Exporter")


    def _open_help(self):
        win = tk.Toplevel(self)
        win.title("Documentation — ADT Map Tile Picker")
        win.configure(bg=COLOR_BG)
        win.resizable(True, True)
        geo = load_config().get("help_geometry", "720x600")
        try: win.geometry(geo)
        except Exception: win.geometry("720x600")
        win.lift()

        save_job = [None]
        def on_cfg(e):
            if e.widget is not win: return
            if save_job[0]: win.after_cancel(save_job[0])
            save_job[0] = win.after(400, lambda: save_config({"help_geometry": win.geometry()}))
        win.bind("<Configure>", on_cfg)

        # Barre titre
        bar = tk.Frame(win, bg=COLOR_BG, padx=14, pady=10)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="Documentation", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 13, "bold")).pack(side=tk.LEFT)
        tk.Button(bar, text="Fermer", command=win.destroy,
                  bg=COLOR_SURFACE, fg=COLOR_TEXT_DIM, relief=tk.FLAT,
                  font=("Consolas", 9), padx=10, pady=3,
                  cursor="hand2", bd=0, activebackground="#333"
                  ).pack(side=tk.RIGHT)
        tk.Frame(win, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Widget Text scrollable — bien plus fiable que canvas+frame sous Windows
        txt_frame = tk.Frame(win, bg=COLOR_BG)
        txt_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        sb = tk.Scrollbar(txt_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt = tk.Text(txt_frame, bg=COLOR_BG, fg=COLOR_TEXT,
                      font=("Consolas", 9), relief=tk.FLAT,
                      yscrollcommand=sb.set, cursor="arrow",
                      state=tk.NORMAL, wrap=tk.WORD,
                      padx=16, pady=8, spacing1=2, spacing3=2)
        sb.config(command=txt.yview)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.bind("<MouseWheel>", lambda e: txt.yview_scroll(-1*(e.delta//120), "units"))

        # Tags de style
        txt.tag_config("section",  foreground="#4A90D9", font=("Consolas", 10, "bold"))
        txt.tag_config("sep",      foreground="#4A90D9")
        txt.tag_config("key",      foreground="#d4881e", font=("Consolas", 9, "bold"),
                       background=COLOR_PANEL)
        txt.tag_config("desc",     foreground=COLOR_TEXT_DIM)
        txt.tag_config("note",     foreground="#666666",  font=("Consolas", 8))
        txt.tag_config("spacer")

        def section(title):
            txt.insert(tk.END, "\n" + title + "\n", "section")
            txt.insert(tk.END, "─" * 60 + "\n", "sep")

        def row(keys, desc):
            txt.insert(tk.END, f"  {keys:<32}", "key")
            txt.insert(tk.END, f"  {desc}\n", "desc")

        def note(text):
            txt.insert(tk.END, f"  ℹ  {text}\n", "note")

        def spacer():
            txt.insert(tk.END, "\n")

        # ── Contenu ───────────────────────────────────────────────────────────
        section("Chargement des ADTs")
        row("+ dossier",                   "Charge tous les .adt d'un dossier → crée un layer")
        row("+ fichiers",                  "Charge des fichiers individuels → crée un layer")
        note("Format attendu : NomMap_X_Y.adt  (ex: Azeroth_27_29.adt)")
        note("Chaque chargement crée un layer indépendant avec une couleur auto-assignée.")
        spacer()

        section("Grille principale — mode normal")
        row("Clic gauche",                 "Sélectionner / désélectionner une tuile")
        row("Clic gauche + drag",          "Sélectionner plusieurs tuiles en zone")
        row("Ctrl + clic/drag",            "Supprimer les tuiles du layer actif (▶ requis)")
        row("Molette  /  + et −",          "Zoom avant / arrière")
        spacer()
        row("Tout sélectionner",           "Sélectionne toutes les tuiles présentes")
        row("Vider sél.",                  "Désélectionne tout")
        row("Inverser",                    "Inverse la sélection")
        spacer()

        section("Grille principale — mode ↔ Déplacer ADTs")
        note("Activer via '↔ Déplacer ADTs'. Sélectionner un layer actif avec ▶ au préalable.")
        spacer()
        row("Drag libre",                  "Déplace spatialement TOUTES les tuiles du layer actif")
        row("Shift + clic",               "Transfère les tuiles dans le brush vers le layer actif")
        row("Ctrl + clic",                 "Supprime les tuiles dans le brush du layer actif")
        note("L'aperçu en temps réel montre les cases fantômes (gris) pendant le drag spatial.")
        note("La sélection est réinitialisée après un déplacement spatial.")
        spacer()

        section("Panel Layers (droite)")
        row("▶",                           "Définir / désélectionner le layer actif (toggle)")
        row("Clic sur le nom",             "Renommer  (Entrée = valider,  Échap = annuler)")
        row("Clic sur le swatch couleur",  "Changer la couleur du layer (colorpicker)")
        row("▲ / ▼",                       "Réordonner les layers (priorité d'export)")
        row("✕",                           "Supprimer le layer et ses données")
        note("Layer en tête de liste = priorité maximale à l'export.")
        note("Bordure blanche sur une tuile = présente dans plusieurs layers simultanément.")
        spacer()

        section("Export")
        row("⚡ Exporter (N)",              "Export rapide vers le dossier mémorisé")
        row("Exporter vers…",              "Choisir un dossier manuellement + mémorise le choix")
        note("Un dialog demande le nom de la map → fichiers renommés  nom_X_Y.adt")
        note("Un second dialog propose de générer un .wdt avec les tiles exportées.")
        note("En cas de chevauchement, le layer le plus haut (prioritaire) est exporté.")
        spacer()

        section("Éditeur WDT  (bouton 'Edit WDT' en haut à droite)")
        row("Charger WDT…",                "Ouvre et parse un fichier .wdt existant")
        row("Nouveau",                     "Crée un WDT vierge")
        row("Enregistrer / Enregistrer sous…", "Sauvegarde le WDT")
        row("Shift + clic/drag",           "Ajoute une tuile dans le MAIN du WDT")
        row("Ctrl  + clic/drag",           "Supprime une tuile du MAIN du WDT")
        row("← Importer depuis layers",    "Remplit le MAIN avec toutes les tuiles des layers")
        note("Vert   = tile présente dans le WDT uniquement")
        note("Bleu   = tile présente dans le WDT ET dans les layers chargés")
        note("Marron = tile dans les layers mais absente du WDT  (incohérence)")
        spacer()

        section("Configuration persistante  (adt_picker_config.json)")
        note("Dossier d'export mémorisé")
        note("Nom de map mémorisé")
        note("Taille et position de chaque fenêtre  (principale, WDT, aide)")
        spacer()

        txt.config(state=tk.DISABLED)   # lecture seule

    def _open_wdt_editor(self):
        win = tk.Toplevel(self)
        win.title("Éditeur WDT")
        win.configure(bg=COLOR_BG)
        win.resizable(True, True)
        # Restaurer géométrie éditeur WDT
        wdt_geo = load_config().get("wdt_editor_geometry", "780x600")
        try: win.geometry(wdt_geo)
        except Exception: win.geometry("780x600")
        win.lift()

        # Debounce géométrie éditeur
        wdt_save_job = [None]
        def on_wdt_configure(event):
            if event.widget is not win: return
            if wdt_save_job[0]: win.after_cancel(wdt_save_job[0])
            wdt_save_job[0] = win.after(400, lambda: save_config({"wdt_editor_geometry": win.geometry()}))
        win.bind("<Configure>", on_wdt_configure)

        state = {"flags":0,"tiles":set(),"wdt_path":None,"flag_vars":{},"modified":False}

        bar = tk.Frame(win, bg=COLOR_BG, padx=10, pady=8)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="Éditeur WDT", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 12, "bold")).pack(side=tk.LEFT)

        btn_save    = tk.Button(bar, text="Enregistrer",    bg="#1a5c38", fg="#fff",
                                 relief=tk.FLAT, font=("Consolas", 9, "bold"),
                                 padx=12, pady=3, cursor="hand2", bd=0,
                                 activebackground="#144d2f", state=tk.DISABLED)
        btn_save.pack(side=tk.RIGHT)
        btn_save_as = tk.Button(bar, text="Enregistrer sous…", bg=COLOR_SURFACE, fg=COLOR_TEXT,
                                 relief=tk.FLAT, font=("Consolas", 9), padx=10, pady=3,
                                 cursor="hand2", bd=0, activebackground="#333", state=tk.DISABLED)
        btn_save_as.pack(side=tk.RIGHT, padx=(0,6))
        btn_new  = tk.Button(bar, text="Nouveau",       bg=COLOR_SURFACE, fg=COLOR_TEXT,
                              relief=tk.FLAT, font=("Consolas", 9), padx=10, pady=3,
                              cursor="hand2", bd=0, activebackground="#333")
        btn_new.pack(side=tk.RIGHT, padx=(0,6))
        btn_load = tk.Button(bar, text="Charger WDT…", bg=COLOR_ACCENT, fg="#fff",
                              relief=tk.FLAT, font=("Consolas", 9, "bold"),
                              padx=10, pady=3, cursor="hand2", bd=0, activebackground="#9a6010")
        btn_load.pack(side=tk.RIGHT, padx=(0,6))
        lbl_path = tk.Label(bar, text="Aucun fichier chargé", bg=COLOR_BG,
                             fg=COLOR_TEXT_DIM, font=("Consolas", 8))
        lbl_path.pack(side=tk.LEFT, padx=(16,0))

        tk.Frame(win, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        body = tk.Frame(win, bg=COLOR_BG)
        body.pack(fill=tk.BOTH, expand=True)

        # Grille
        grid_wrap = tk.Frame(body, bg=COLOR_BG)
        grid_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
        tk.Label(grid_wrap,
                 text="Grille MAIN  —  Shift+clic = ajouter  |  Ctrl+clic = supprimer",
                 bg=COLOR_BG, fg=COLOR_TEXT_DIM, font=("Consolas", 8)).pack(anchor="w")

        gc  = tk.Canvas(grid_wrap, bg=COLOR_SURFACE, highlightthickness=0)
        gsv = tk.Scrollbar(grid_wrap, orient=tk.VERTICAL,   command=gc.yview)
        gsh = tk.Scrollbar(grid_wrap, orient=tk.HORIZONTAL, command=gc.xview)
        gc.configure(yscrollcommand=gsv.set, xscrollcommand=gsh.set)
        gsh.pack(side=tk.BOTTOM, fill=tk.X)
        gsv.pack(side=tk.RIGHT,  fill=tk.Y)
        gc.pack(fill=tk.BOTH, expand=True)

        # Tooltip WDT
        tt = tk.Toplevel(win); tt.wm_overrideredirect(True); tt.withdraw()
        tt.configure(bg="#111111")
        lbl_tt = tk.Label(tt, bg="#111111", fg="#aaaaaa", font=("Consolas",9), padx=6, pady=3)
        lbl_tt.pack()

        WCELL=8; WGAP=1; WSTEP=WCELL+WGAP
        wdt_built = [False]

        def wdt_cell_color(key):
            in_wdt    = key in state["tiles"]
            in_layers = any(key in l.file_map for l in self.layers)
            if in_wdt and in_layers: return "#4A90D9","#6aaeed"
            if in_wdt:               return "#1D9E75","#1D9E75"
            if in_layers:            return "#553300","#885500"
            return COLOR_EMPTY, COLOR_EMPTY_BD

        def draw_wdt_grid():
            if not wdt_built[0]:
                gc.delete("all")
                t = WSTEP*64
                gc.configure(scrollregion=(0,0,t,t))
                for y in range(64):
                    for x in range(64):
                        key=f"{x}_{y}"; x0,y0=x*WSTEP,y*WSTEP
                        f,b=wdt_cell_color(key)
                        gc.create_rectangle(x0,y0,x0+WCELL,y0+WCELL,fill=f,outline=b,
                                            tags=(f"wc_{key}","wcell"))
                wdt_built[0]=True
            else:
                for y in range(64):
                    for x in range(64):
                        key=f"{x}_{y}"; f,b=wdt_cell_color(key)
                        gc.itemconfig(f"wc_{key}",fill=f,outline=b)

        def on_wdt_click(event):
            x=int(gc.canvasx(event.x))//WSTEP; y=int(gc.canvasy(event.y))//WSTEP
            if not(0<=x<64 and 0<=y<64): return
            key=f"{x}_{y}"
            if event.state&0x0001:   state["tiles"].add(key)
            elif event.state&0x0004: state["tiles"].discard(key)
            else: return
            lbl_tc.config(text=f"{len(state['tiles'])} tiles")
            state["modified"]=True
            if state["wdt_path"]: btn_save.config(state=tk.NORMAL)
            f,b=wdt_cell_color(key)
            gc.itemconfig(f"wc_{key}",fill=f,outline=b)

        def on_wdt_hover(event):
            x=int(gc.canvasx(event.x))//WSTEP; y=int(gc.canvasy(event.y))//WSTEP
            if 0<=x<64 and 0<=y<64:
                key=f"{x}_{y}"
                parts=[]
                if key in state["tiles"]: parts.append("WDT")
                if any(key in l.file_map for l in self.layers): parts.append("layers")
                lbl_tt.config(text=f"[{x},{y}]" + (f"  —  {' + '.join(parts)}" if parts else ""))
                tt.wm_geometry(f"+{win.winfo_rootx()+event.x+14}+{win.winfo_rooty()+event.y+40}")
                tt.deiconify()
            else: tt.withdraw()

        gc.bind("<ButtonPress-1>", on_wdt_click)
        gc.bind("<B1-Motion>",     on_wdt_click)
        gc.bind("<Motion>",        on_wdt_hover)
        gc.bind("<Leave>",         lambda e: tt.withdraw())

        # Légende
        leg=tk.Frame(grid_wrap,bg=COLOR_BG); leg.pack(anchor="w",pady=(4,0))
        for col,lbl in [("#1D9E75","WDT uniquement"),("#4A90D9","WDT + layers"),("#553300","layers seulement")]:
            d=tk.Canvas(leg,width=10,height=10,bg=COLOR_BG,highlightthickness=0)
            d.create_rectangle(0,0,10,10,fill=col,outline=""); d.pack(side=tk.LEFT,padx=(0,4))
            tk.Label(leg,text=lbl,bg=COLOR_BG,fg=COLOR_TEXT_DIM,font=("Consolas",8)).pack(side=tk.LEFT,padx=(0,14))

        # Panel flags
        tk.Frame(body,bg=COLOR_SEP,width=1).pack(side=tk.LEFT,fill=tk.Y)
        fp=tk.Frame(body,bg=COLOR_PANEL,width=300); fp.pack(side=tk.LEFT,fill=tk.Y); fp.pack_propagate(False)
        tk.Label(fp,text="FLAGS MPHD",bg=COLOR_PANEL,fg=COLOR_TEXT_DIM,
                 font=("Consolas",9,"bold"),padx=12,pady=8).pack(anchor="w")
        tk.Frame(fp,bg=COLOR_SEP,height=1).pack(fill=tk.X)
        lbl_tot=tk.Label(fp,text="Total : 0x0000",bg=COLOR_PANEL,fg=COLOR_TEXT_DIM,
                          font=("Consolas",9),padx=12,pady=4); lbl_tot.pack(anchor="w")

        def update_total(*_):
            val=sum(bit for bit,var in state["flag_vars"].items() if var.get())
            lbl_tot.config(text=f"Total : 0x{val:04X}  ({val})")
            state["modified"]=True
            if state["wdt_path"]: btn_save.config(state=tk.NORMAL)

        for bit,label in MPHD_FLAGS:
            var=tk.BooleanVar(value=False); state["flag_vars"][bit]=var
            var.trace_add("write",update_total)
            tk.Checkbutton(fp,text=label,variable=var,bg=COLOR_PANEL,fg=COLOR_TEXT,
                           selectcolor=COLOR_SURFACE,activebackground=COLOR_PANEL,
                           font=("Consolas",9),anchor="w").pack(fill=tk.X,padx=12,pady=1)

        tk.Frame(fp,bg=COLOR_SEP,height=1).pack(fill=tk.X,pady=(8,0))
        tk.Label(fp,text="Tiles dans MAIN :",bg=COLOR_PANEL,fg=COLOR_TEXT_DIM,
                 font=("Consolas",8),padx=12,pady=4).pack(anchor="w")
        lbl_tc=tk.Label(fp,text="0 tiles",bg=COLOR_PANEL,fg=COLOR_TEXT,
                         font=("Consolas",10,"bold"),padx=12); lbl_tc.pack(anchor="w")
        tk.Button(fp,text="← Importer depuis les layers",
                  command=lambda: [state["tiles"].__ior__(k for l in self.layers for k in l.file_map),
                                   lbl_tc.config(text=f"{len(state['tiles'])} tiles"),
                                   draw_wdt_grid()],
                  bg=COLOR_SURFACE,fg=COLOR_TEXT_DIM,relief=tk.FLAT,font=("Consolas",8),
                  padx=10,pady=4,cursor="hand2",bd=0,activebackground="#2a2a2a"
                  ).pack(anchor="w",padx=12,pady=(4,0))

        def refresh_flags():
            for bit,var in state["flag_vars"].items(): var.set(bool(state["flags"]&bit))

        def do_load(path=None):
            p=path or filedialog.askopenfilename(title="Charger WDT",
                filetypes=[("WDT","*.wdt"),("All","*.*")])
            if not p: return
            try:
                flags,tiles=parse_wdt(open(p,"rb").read())
                state.update({"flags":flags,"tiles":tiles,"wdt_path":p,"modified":False})
                refresh_flags(); lbl_tc.config(text=f"{len(tiles)} tiles")
                lbl_path.config(text=("…"+p[-54:] if len(p)>55 else p),fg=COLOR_TEXT)
                btn_save.config(state=tk.NORMAL); btn_save_as.config(state=tk.NORMAL)
                draw_wdt_grid()
            except Exception as e:
                messagebox.showerror("Erreur",f"Impossible de lire le WDT :\n{e}",parent=win)

        def do_new():
            state.update({"flags":0,"tiles":set(),"wdt_path":None,"modified":False})
            for v in state["flag_vars"].values(): v.set(False)
            lbl_tc.config(text="0 tiles"); lbl_path.config(text="Nouveau WDT",fg=COLOR_TEXT_DIM)
            btn_save.config(state=tk.DISABLED); btn_save_as.config(state=tk.NORMAL)
            draw_wdt_grid()

        def write_wdt(path):
            flags=sum(bit for bit,var in state["flag_vars"].items() if var.get())
            data=generate_wdt(state["tiles"],mphd_flags=flags)
            open(path,"wb").write(data)
            state["wdt_path"]=path; state["modified"]=False
            lbl_path.config(text=("…"+path[-54:] if len(path)>55 else path),fg=COLOR_TEXT)
            messagebox.showinfo("Enregistré",f"WDT enregistré :\n{path}",parent=win)

        def do_save():
            if state["wdt_path"]: write_wdt(state["wdt_path"])
        def do_save_as():
            p=filedialog.asksaveasfilename(title="Enregistrer WDT",
                defaultextension=".wdt",filetypes=[("WDT","*.wdt"),("All","*.*")])
            if p: write_wdt(p)

        btn_load.config(command=do_load); btn_new.config(command=do_new)
        btn_save.config(command=do_save); btn_save_as.config(command=do_save_as)
        draw_wdt_grid()
        win.protocol("WM_DELETE_WINDOW", lambda: (tt.destroy(), win.destroy()))

    # ─── Export ──────────────────────────────────────────────────────────────

    def _ask_export_name(self, action_label="Exporter"):
        """Ouvre un dialog pour saisir/confirmer le nom d'export. Retourne le nom ou None."""
        dialog = tk.Toplevel(self)
        dialog.title("Nom de la map")
        dialog.configure(bg=COLOR_BG)
        dialog.resizable(False, False)
        dialog.geometry(f"+{self.winfo_rootx()+200}+{self.winfo_rooty()+200}")
        dialog.grab_set()
        dialog.lift()

        tk.Label(dialog, text="Nom de la map pour l'export :",
                 bg=COLOR_BG, fg=COLOR_TEXT, font=("Consolas", 10)
                 ).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(dialog, text="Les fichiers seront renommés  nom_X_Y.adt",
                 bg=COLOR_BG, fg=COLOR_TEXT_DIM, font=("Consolas", 8)
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        var = tk.StringVar(value=self.export_name)
        entry = tk.Entry(dialog, textvariable=var, bg=COLOR_SURFACE, fg=COLOR_TEXT,
                         insertbackground=COLOR_TEXT, relief=tk.FLAT,
                         font=("Consolas", 11), width=22)
        entry.pack(padx=14, pady=(0, 12))
        dialog.after(50, lambda: (entry.focus_force(), entry.select_range(0, tk.END)))

        result = [None]

        def confirm(e=None):
            name = var.get().strip()
            # Nettoyer : garder uniquement lettres, chiffres, underscores, tirets
            import re as _re
            name = _re.sub(r'[^\w\-]', '_', name).strip('_')
            if name:
                result[0] = name
                # Mémoriser
                self.export_name = name
                save_config({"export_name": name})
                self._refresh_folder_ui()
            dialog.destroy()

        def cancel(e=None):
            dialog.destroy()

        entry.bind("<Return>", confirm)
        entry.bind("<Escape>", cancel)

        btn_row = tk.Frame(dialog, bg=COLOR_BG)
        btn_row.pack(pady=(0, 14))
        tk.Button(btn_row, text="Annuler", command=cancel,
                  bg=COLOR_SURFACE, fg=COLOR_TEXT, relief=tk.FLAT,
                  font=("Consolas", 10), padx=14, pady=4, cursor="hand2", bd=0,
                  activebackground="#333"
                  ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btn_row, text=action_label, command=confirm,
                  bg=COLOR_ACCENT, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 10, "bold"), padx=14, pady=4,
                  cursor="hand2", bd=0, activebackground="#9a6010"
                  ).pack(side=tk.LEFT)

        dialog.wait_window()
        return result[0]

    def _ask_wdt_options(self, map_name):
        """Dialog pour choisir si on génère un WDT et quels flags. Retourne (bool, int) ou None."""
        dialog = tk.Toplevel(self)
        dialog.title("Générer un WDT ?")
        dialog.configure(bg=COLOR_BG)
        dialog.resizable(False, False)
        dialog.geometry(f"+{self.winfo_rootx()+200}+{self.winfo_rooty()+200}")
        dialog.grab_set()
        dialog.lift()

        tk.Label(dialog, text=f"Générer  {map_name}.wdt  ?",
                 bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 11, "bold")).pack(padx=20, pady=(16, 4))
        tk.Label(dialog, text="Le WDT listera les tiles exportées dans la grille 64×64.",
                 bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 8)).pack(padx=20, pady=(0, 10))

        # Flags MPHD
        tk.Label(dialog, text="Flags MPHD :", bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                 font=("Consolas", 9)).pack(anchor="w", padx=20)

        flag_defs = MPHD_FLAGS
        flag_vars = {}
        for bit, label in flag_defs:
            var = tk.BooleanVar(value=False)
            flag_vars[bit] = var
            tk.Checkbutton(dialog, text=label, variable=var,
                           bg=COLOR_BG, fg=COLOR_TEXT_DIM,
                           selectcolor=COLOR_SURFACE, activebackground=COLOR_BG,
                           font=("Consolas", 9)).pack(anchor="w", padx=30)

        result = [None]

        def confirm_wdt():
            flags = sum(bit for bit, var in flag_vars.items() if var.get())
            result[0] = (True, flags)
            dialog.destroy()

        def skip_wdt():
            result[0] = (False, 0)
            dialog.destroy()

        def cancel():
            dialog.destroy()

        tk.Frame(dialog, bg=COLOR_SEP, height=1).pack(fill=tk.X, pady=(10, 0))
        btn_row = tk.Frame(dialog, bg=COLOR_BG)
        btn_row.pack(pady=12, padx=20, fill=tk.X)

        tk.Button(btn_row, text="Annuler", command=cancel,
                  bg=COLOR_SURFACE, fg=COLOR_TEXT, relief=tk.FLAT,
                  font=("Consolas", 9), padx=10, pady=4,
                  cursor="hand2", bd=0, activebackground="#333"
                  ).pack(side=tk.LEFT)
        tk.Button(btn_row, text="Sans WDT", command=skip_wdt,
                  bg=COLOR_SURFACE, fg=COLOR_TEXT_DIM, relief=tk.FLAT,
                  font=("Consolas", 9), padx=10, pady=4,
                  cursor="hand2", bd=0, activebackground="#333"
                  ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(btn_row, text="Générer le WDT", command=confirm_wdt,
                  bg="#1a5c38", fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=12, pady=4,
                  cursor="hand2", bd=0, activebackground="#144d2f"
                  ).pack(side=tk.RIGHT)

        dialog.wait_window()
        return result[0]

    def _do_export(self, dest, map_name):
        """Copie les fichiers sélectionnés vers dest en les renommant nom_X_Y.adt.
        Propose ensuite de générer un WDT."""
        # Demander WDT
        wdt_result = self._ask_wdt_options(map_name)
        if wdt_result is None:
            return   # annulé

        merged = self._merged_file_map()
        copied = 0
        errors = []
        for key in self.selected:
            src = merged.get(key)
            if not src: continue
            x, y = key.split("_")
            dst_name = f"{map_name}_{x}_{y}.adt"
            try:
                shutil.copy2(src, os.path.join(dest, dst_name))
                copied += 1
            except Exception as e:
                errors.append(f"{dst_name}: {e}")

        # Générer WDT si demandé
        wdt_msg = ""
        make_wdt, wdt_flags = wdt_result
        if make_wdt and copied > 0:
            try:
                wdt_data = generate_wdt(self.selected, mphd_flags=wdt_flags)
                wdt_path = os.path.join(dest, f"{map_name}.wdt")
                with open(wdt_path, "wb") as f:
                    f.write(wdt_data)
                wdt_msg = f"\nWDT généré : {map_name}.wdt  (flags=0x{wdt_flags:04X})"
            except Exception as e:
                errors.append(f"WDT: {e}")

        if errors:
            messagebox.showwarning("Export partiel",
                f"{copied} ADT copié(s).{wdt_msg}\n\nErreurs :\n" + "\n".join(errors))
        else:
            messagebox.showinfo("Export terminé",
                f"{copied} fichier{'s' if copied>1 else ''} copié{'s' if copied>1 else ''} vers :\n{dest}"
                f"\nNom : {map_name}_X_Y.adt{wdt_msg}")

    def _export_fast(self):
        if not self.selected or not self.export_folder: return
        if not os.path.isdir(self.export_folder):
            messagebox.showerror("Dossier introuvable",
                f"Le dossier mémorisé n'existe plus :\n{self.export_folder}")
            self._export(); return
        name = self._ask_export_name()
        if name:
            self._do_export(self.export_folder, name)

    def _export(self):
        if not self.selected: return
        dest = filedialog.askdirectory(title="Choisir le dossier de destination")
        if not dest: return
        self.export_folder = dest
        save_config({"export_folder": dest})
        self._refresh_folder_ui()
        name = self._ask_export_name()
        if name:
            self._do_export(dest, name)

    def _pick_folder(self):
        f = filedialog.askdirectory(title="Choisir le dossier d'export mémorisé")
        if not f: return
        self.export_folder = f
        save_config({"export_folder": f})
        self._refresh_folder_ui()

    def _pick_export_name(self):
        self._ask_export_name(action_label="Enregistrer")

    def _clear_export_name(self):
        self.export_name = ""
        save_config({"export_name": ""})
        self._refresh_folder_ui()

    def _clear_folder(self):
        self.export_folder = ""
        save_config({"export_folder": ""})
        self._refresh_folder_ui()

    def _refresh_folder_ui(self):
        if self.export_folder:
            d = self.export_folder
            self.var_folder.set("…" + d[-44:] if len(d) > 45 else d)
            self.lbl_folder.config(fg=COLOR_TEXT)
        else:
            self.var_folder.set("aucun  (cliquer pour définir)")
            self.lbl_folder.config(fg=COLOR_TEXT_DIM)
        if hasattr(self, "var_name"):
            if self.export_name:
                self.var_name.set(self.export_name)
                self.lbl_name_mem.config(fg=COLOR_TEXT)
            else:
                self.var_name.set("aucun  (cliquer)")
                self.lbl_name_mem.config(fg=COLOR_TEXT_DIM)
        self._update_stats()

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ADTPickerApp()
    app.mainloop()
